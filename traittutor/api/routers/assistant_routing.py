"""Authenticated unified capability-routing API.

This endpoint decides and records a capability.  It intentionally stops at a
typed receipt for costly actions so downstream resource owners retain their
own validation, idempotency, and audit boundaries.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import hashlib
import json
from typing import Annotated, Any, Literal, NoReturn

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from traittutor import learning_packs
from traittutor.assistant_routing.models import Capability, CapabilityDecision, SearchReceipt
from traittutor.assistant_routing.search_delivery import deliver_search_to_thread
from traittutor.assistant_routing.service import CapabilityRoutingService
from traittutor.assistant_routing.store import (
    CapabilityDecisionIdempotencyConflict,
    CapabilityDecisionNotFound,
    CapabilityDecisionStore,
    CapabilityDecisionStoreError,
)
from traittutor.generate.service import GenerationRequest, MaterialSource
from traittutor.generate.tasks import GenerationTaskManager, get_generation_task_manager
from traittutor.learning.intent import scan_untrusted_learning_payload
from traittutor.learning_components import build_learning_component_plan
from traittutor.multi_user.context import get_current_user
from traittutor.multi_user.models import CurrentUser
from traittutor.multi_user.tool_access import allowed_optional_tools
from traittutor.research_workspace.scheduler import ResearchRunScheduler
from traittutor.research_workspace.service import ResearchWorkspaceService
from traittutor.research_workspace.store import ResearchWorkspaceStoreError
from traittutor.services.session import get_session_store
from traittutor.services.session.protocol import SessionStoreProtocol
from traittutor.tools.builtin import WebSearchTool

router = APIRouter(prefix="/assistant/route")


class RouteRequest(BaseModel):
    """One browser-safe route command; identity is always server-derived."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    message: str = Field(min_length=1, max_length=4_000)
    session_id: str | None = Field(default=None, max_length=128)
    requested_capability: Capability | None = None
    idempotency_key: str = Field(min_length=1, max_length=160)


class ConfirmRouteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    idempotency_key: str = Field(min_length=1, max_length=160)
    # A decision keeps only a digest of the original message.  The question
    # belongs in the immutable ResearchBrief, supplied after explicit consent.
    research_question: str | None = Field(default=None, min_length=1, max_length=12_000)
    workspace_title: str | None = Field(default=None, min_length=1, max_length=240)
    learning_goal: str | None = Field(default=None, min_length=1, max_length=4_000)
    generation_goal: str | None = Field(default=None, min_length=1, max_length=4_000)
    material: "CreateMaterialReference | None" = None


class CreateMaterialReference(BaseModel):
    """One explicit, resolvable source for a confirmed courseware task.

    This intentionally mirrors the public generation-source shape without
    accepting a generic action or inferred material.  The existing resolver
    remains the final owner-aware source check in the generation worker.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_type: Literal["knowledge", "notebook", "upload", "paste"]
    text: str = Field(default="", max_length=600_000)
    title: str = Field(default="Untitled material", min_length=1, max_length=240)
    source_id: str | None = Field(default=None, min_length=1, max_length=240)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DecisionPublic(BaseModel):
    """No raw user input, owner identity, or private idempotency material."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    decision_id: str
    capability: Capability
    requested_capability: Capability | None
    manual_override: bool
    status: str
    requires_confirmation: bool
    action_target: dict[str, object]
    reason: str
    fallback_from: Capability | None
    revision: int
    created_at: str
    updated_at: str


class RouteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: DecisionPublic | None = None
    blocked: bool = False
    block_code: str | None = None
    search_receipt: SearchReceipt | None = None
    replayed: bool = False


class ConfirmRouteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: DecisionPublic
    replayed: bool
    workspace_id: str | None = None
    brief_id: str | None = None
    run_id: str | None = None
    pack_id: str | None = None
    plan_id: str | None = None
    generation_id: str | None = None
    events_url: str | None = None
    result_url: str | None = None


CapabilityDecisionStoreFactory = Callable[[CurrentUser], CapabilityDecisionStore]
SearchExecutor = Callable[[str], Awaitable[dict[str, object]]]
ResearchServiceFactory = Callable[[CurrentUser], ResearchWorkspaceService]
ResearchSchedulerFactory = Callable[[CurrentUser], ResearchRunScheduler]
GenerationTaskManagerFactory = Callable[[], GenerationTaskManager]
SessionStoreFactory = Callable[[], SessionStoreProtocol]

_SEARCH_DELIVERY_LOCKS: dict[tuple[str, str], asyncio.Lock] = {}


def default_capability_decision_store_factory(user: CurrentUser) -> CapabilityDecisionStore:
    return CapabilityDecisionStore(user.id)


capability_decision_store_factory: CapabilityDecisionStoreFactory = (
    default_capability_decision_store_factory
)


async def default_search_executor(query: str) -> dict[str, object]:
    """Execute only the registered built-in tool, respecting user grants."""
    allowed = allowed_optional_tools()
    if allowed is not None and "web_search" not in allowed:
        raise PermissionError("web_search is not available to this user")
    result = await WebSearchTool().execute(query=query)
    return {"content": result.content, "sources": result.sources}


search_executor: SearchExecutor = default_search_executor


def _default_research_service_factory(user: CurrentUser) -> ResearchWorkspaceService:
    """Use the same owner-bound composition as the Research Workspace API."""
    from traittutor.research_workspace.store import ResearchWorkspaceStore

    return ResearchWorkspaceService(ResearchWorkspaceStore(user.id))


def _default_research_scheduler_factory(user: CurrentUser) -> ResearchRunScheduler:
    """Reuse the Research router's production worker composition lazily."""
    from traittutor.api.routers.research_workspace import default_research_scheduler_factory

    return default_research_scheduler_factory(user)


research_service_factory: ResearchServiceFactory = _default_research_service_factory
research_scheduler_factory: ResearchSchedulerFactory = _default_research_scheduler_factory
generation_task_manager_factory: GenerationTaskManagerFactory = get_generation_task_manager
session_store_factory: SessionStoreFactory = get_session_store


def get_capability_routing_service() -> CapabilityRoutingService:
    return CapabilityRoutingService(
        capability_decision_store_factory(get_current_user()),
        search_executor=search_executor,
    )


def get_research_workspace_service() -> ResearchWorkspaceService:
    return research_service_factory(get_current_user())


def get_research_run_scheduler() -> ResearchRunScheduler:
    return research_scheduler_factory(get_current_user())


def get_generation_task_manager_for_create() -> GenerationTaskManager:
    """Reuse the normal owner-bound generation queue for confirmed Create."""
    return generation_task_manager_factory()


def get_assistant_route_session_store() -> SessionStoreProtocol:
    """Reuse the authenticated canonical session backend for Search delivery."""
    return session_store_factory()


CapabilityRoutingServiceDependency = Annotated[
    CapabilityRoutingService, Depends(get_capability_routing_service)
]
ResearchServiceDependency = Annotated[
    ResearchWorkspaceService, Depends(get_research_workspace_service)
]
ResearchSchedulerDependency = Annotated[ResearchRunScheduler, Depends(get_research_run_scheduler)]
CreateGenerationTaskManagerDependency = Annotated[
    GenerationTaskManager, Depends(get_generation_task_manager_for_create)
]
AssistantRouteSessionStoreDependency = Annotated[
    SessionStoreProtocol, Depends(get_assistant_route_session_store)
]


def _public(value: CapabilityDecision) -> DecisionPublic:
    return DecisionPublic(
        decision_id=value.decision_id,
        capability=value.capability,
        requested_capability=value.requested_capability,
        manual_override=value.manual_override,
        status=value.status,
        requires_confirmation=value.requires_confirmation,
        action_target=value.action_target,
        reason=value.reason,
        fallback_from=value.fallback_from,
        revision=value.revision,
        created_at=value.created_at,
        updated_at=value.updated_at,
    )


def _raise_store_error(exc: Exception) -> NoReturn:
    if isinstance(exc, CapabilityDecisionNotFound):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Capability decision not found"
        ) from exc
    if isinstance(exc, CapabilityDecisionIdempotencyConflict):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="idempotency key conflict"
        ) from exc
    if isinstance(exc, (CapabilityDecisionStoreError, ValueError)):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    raise exc


def _research_ids(decision: CapabilityDecision) -> tuple[str | None, str | None, str | None]:
    """Read only the public hand-off receipt from a completed decision."""
    target = decision.action_target
    values = tuple(target.get(name) for name in ("workspace_id", "brief_id", "run_id"))
    if all(isinstance(value, str) and value for value in values):
        return values  # type: ignore[return-value]
    return None, None, None


def _learn_ids(decision: CapabilityDecision) -> tuple[str | None, str | None]:
    """Read the canonical Pack/Plan receipt from a completed Learn target."""
    target = decision.action_target
    values = tuple(target.get(name) for name in ("pack_id", "plan_id"))
    if all(isinstance(value, str) and value for value in values):
        return values  # type: ignore[return-value]
    return None, None


def _normalized_research_input(payload: ConfirmRouteRequest) -> tuple[str, str]:
    """Validate a post-consent research brief before any product write."""
    question = (payload.research_question or "Research request").strip()
    title = (payload.workspace_title or "Research workspace").strip()
    if not question or not title:
        raise ValueError("research question and workspace title must not be blank")
    action, category = scan_untrusted_learning_payload({"message": question})
    if action != "allow":
        raise ValueError(f"unsafe research input: {category or 'unsafe_input'}")
    return question, title


def _normalized_create_request(
    payload: ConfirmRouteRequest,
    *,
    decision: CapabilityDecision,
) -> GenerationRequest:
    """Build a strict courseware task only from freshly screened input.

    A route decision intentionally retains a digest, so it cannot safely
    reconstruct a generation goal or material.  Both must be supplied after
    consent, validated before the decision advances, and remain in the
    owner-bound generation task rather than the public routing receipt.
    """
    goal = (payload.generation_goal or "").strip()
    material = payload.material
    if not goal:
        raise ValueError("create confirmation requires generation_goal")
    if material is None:
        raise ValueError("create confirmation requires a material reference")
    if material.source_type == "paste" and not material.text.strip():
        raise ValueError("paste material requires non-empty text")
    if material.source_type != "paste" and not (material.source_id or "").strip():
        raise ValueError(f"{material.source_type} material requires source_id")
    action, category = scan_untrusted_learning_payload(
        {"generation_goal": goal, "material": material.model_dump()}
    )
    if action != "allow":
        raise ValueError(f"unsafe create input: {category or 'unsafe_input'}")
    options: dict[str, object] = {
        "instruction": goal,
        "assistant_routing_contract": "confirmed-create.v1",
    }
    if decision.session_id:
        # The route session is an opaque correlation handle, not an owner
        # supplied identity.  Generation derives the user from request context.
        options["session_id"] = decision.session_id
    return GenerationRequest(
        generation_type="courseware",
        material=MaterialSource(**material.model_dump()),
        options=options,
    )


def _create_confirmation_input_hash(payload: ConfirmRouteRequest) -> str:
    """Pin every retry to the exact re-scanned Create contract, without text."""
    material = payload.material
    if material is None:
        raise ValueError("create confirmation requires a material reference")
    canonical = json.dumps(
        {
            "generation_goal": (payload.generation_goal or "").strip(),
            "material": material.model_dump(mode="json"),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _start_confirmed_research(
    decision: CapabilityDecision,
    payload: ConfirmRouteRequest,
    *,
    service: CapabilityRoutingService,
    research_service: ResearchWorkspaceService,
) -> tuple[CapabilityDecision, bool, str, str, str]:
    """Create exactly one owner-bound workspace/brief/run from consent.

    Every Research Workspace operation derives an immutable idempotency key
    from the routed decision.  A retry after an interrupted request therefore
    resolves the original records rather than creating a second run.
    """
    if research_service.owner_id != decision.owner_id:
        raise ValueError("research service owner does not match capability decision")
    existing_ids = _research_ids(decision)
    if decision.status == "completed" and all(existing_ids):
        workspace_id, brief_id, run_id = existing_ids
        assert workspace_id is not None and brief_id is not None and run_id is not None
        return decision, True, workspace_id, brief_id, run_id
    question, title = _normalized_research_input(payload)
    key_prefix = f"capability-research:{decision.decision_id}"
    workspace = research_service.create_workspace(
        title=title,
        subject_id=None,
        idempotency_key=f"{key_prefix}:workspace",
    )
    brief = research_service.save_brief(
        workspace.workspace_id,
        question=question,
        expected_workspace_revision=workspace.revision,
        idempotency_key=f"{key_prefix}:brief",
    )
    run = research_service.start_run(
        workspace.workspace_id,
        brief_id=brief.brief_id,
        brief_version=brief.version,
        idempotency_key=f"{key_prefix}:run",
    )
    completed, replayed = service.complete_research_action(
        decision.decision_id,
        workspace_id=workspace.workspace_id,
        brief_id=brief.brief_id,
        run_id=run.run_id,
    )
    return completed, replayed, workspace.workspace_id, brief.brief_id, run.run_id


def _start_confirmed_learn(
    decision: CapabilityDecision,
    payload: ConfirmRouteRequest,
    *,
    service: CapabilityRoutingService,
) -> tuple[CapabilityDecision, bool, str, str]:
    """Create one owner-bound Pack and component plan after consent.

    The current request already runs in an authenticated user's workspace;
    checking that owner again prevents an injected factory or stale context
    from writing a Pack for a different decision owner.  The Pack helper uses
    the decision ID as an opaque durable replay key under the Pack file lock.
    The browser resubmits the exact visible learning goal after consent; the
    routing ledger stores only its digest while the Pack owns the goal text.
    """
    if get_current_user().id != decision.owner_id:
        raise ValueError("learning pack owner does not match capability decision")
    existing_ids = _learn_ids(decision)
    if decision.status == "completed" and all(existing_ids):
        pack_id, plan_id = existing_ids
        assert pack_id is not None and plan_id is not None
        return decision, True, pack_id, plan_id

    learning_goal = (payload.learning_goal or "").strip()
    if not learning_goal:
        raise ValueError("learn confirmation requires learning_goal")
    action, category = scan_untrusted_learning_payload({"message": learning_goal})
    if action != "allow":
        raise ValueError(f"unsafe learning goal: {category or 'unsafe_input'}")

    pack, _pack_replayed = learning_packs.create_capability_routed_pack_or_replay(
        decision.decision_id,
        learning_goal=learning_goal,
    )
    pack_id = str(pack["pack_id"])
    plan_id = str(pack.get("active_plan_id") or "")
    plan = learning_packs.get_component_plan(pack_id, plan_id) if plan_id else None
    if plan is None:
        # This deterministic selector only reads existing scoped support
        # evidence. It appends no LearnerEvent and cannot update BKT.
        generated_plan = build_learning_component_plan(
            pack,
            instruction=learning_goal,
        )
        plan = learning_packs.create_component_plan(pack_id, generated_plan.model_dump())
    if plan is None:
        raise ValueError("learning plan could not be created")
    plan_id = str(plan.get("plan_id") or "")
    if not plan_id:
        raise ValueError("learning plan could not be created")
    completed, replayed = service.complete_learn_action(
        decision.decision_id,
        pack_id=pack_id,
        plan_id=plan_id,
    )
    return completed, replayed, pack_id, plan_id


def _create_generation_id(decision: CapabilityDecision) -> str:
    """Return a queue-safe idempotency identity that clients cannot choose."""
    return f"capability-create-{decision.decision_id}"


def _start_confirmed_create(
    decision: CapabilityDecision,
    payload: ConfirmRouteRequest,
    *,
    service: CapabilityRoutingService,
    task_manager: GenerationTaskManager,
) -> tuple[CapabilityDecision, bool, str]:
    """Queue exactly one owner-derived courseware task after re-screening."""
    if get_current_user().id != decision.owner_id:
        raise ValueError("generation task owner does not match capability decision")
    existing_id = decision.action_target.get("generation_id")
    if decision.status == "completed" and isinstance(existing_id, str) and existing_id:
        return decision, True, existing_id
    request = _normalized_create_request(payload, decision=decision)
    generation_id = _create_generation_id(decision)
    task = task_manager.create_idempotent(request, generation_id=generation_id)
    completed, replayed = service.complete_create_action(
        decision.decision_id,
        generation_id=task.generation_id,
    )
    return completed, replayed, task.generation_id


@router.post("", response_model=RouteResponse)
async def route_capability(
    payload: RouteRequest,
    service: CapabilityRoutingServiceDependency,
    session_store: AssistantRouteSessionStoreDependency,
) -> RouteResponse:
    """Safely decide this turn's capability and, only for search, invoke its tool."""
    try:
        if payload.session_id and await session_store.get_session(payload.session_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
        decision, replayed, blocked = await service.route(
            message=payload.message,
            session_id=payload.session_id,
            requested_capability=payload.requested_capability,
            idempotency_key=payload.idempotency_key,
        )
    except Exception as exc:
        _raise_store_error(exc)
    if blocked is not None:
        return RouteResponse(
            blocked=True,
            block_code=str(blocked["code"]),
            replayed=False,
        )
    assert decision is not None
    if decision.capability == "search" and decision.search_receipt is not None:
        lock_key = (decision.owner_id, decision.decision_id)
        lock = _SEARCH_DELIVERY_LOCKS.setdefault(lock_key, asyncio.Lock())
        async with lock:
            try:
                current = service.get(decision.decision_id)
                receipt = current.search_receipt
                if receipt is not None and receipt.message_id is None:
                    if current.session_id is None:
                        session = await session_store.ensure_session()
                        session_id = str(
                            session.get("session_id") or session.get("id") or ""
                        ).strip()
                        if not session_id:
                            raise RuntimeError("session store returned no session ID")
                        current, _ = service.bind_search_session(
                            current.decision_id,
                            session_id=session_id,
                        )
                    delivery = await deliver_search_to_thread(
                        store=session_store,
                        decision=current,
                        query=payload.message.strip(),
                    )
                    current, _ = service.record_search_delivery(
                        current.decision_id,
                        session_id=delivery.session_id,
                        user_message_id=delivery.user_message_id,
                        message_id=delivery.message_id,
                    )
                decision = current
            except ValueError as exc:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
            except Exception as exc:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Search completed but could not be delivered to the conversation",
                ) from exc
    return RouteResponse(
        decision=_public(decision),
        search_receipt=decision.search_receipt,
        replayed=replayed,
    )


@router.post("/{decision_id}/confirm", response_model=ConfirmRouteResponse)
async def confirm_capability_route(
    decision_id: str,
    payload: ConfirmRouteRequest,
    service: CapabilityRoutingServiceDependency,
    research_service: ResearchServiceDependency,
    research_scheduler: ResearchSchedulerDependency,
    task_manager: CreateGenerationTaskManagerDependency,
    background_tasks: BackgroundTasks,
) -> ConfirmRouteResponse:
    """Record consent and execute the bounded, confirmed capability action."""
    try:
        # The first route already scans its message, but confirmation carries
        # the eventual immutable ResearchBrief text.  Scan that separately
        # before recording consent so a risky replacement cannot leave any
        # decision, workspace, brief, or run side effect.
        current = service.get(decision_id)
        create_input_hash: str | None = None
        if current.capability == "research" and current.status != "completed":
            _normalized_research_input(payload)
        if current.capability == "create" and (
            current.status != "completed"
            or payload.generation_goal is not None
            or payload.material is not None
        ):
            _normalized_create_request(payload, decision=current)
            create_input_hash = _create_confirmation_input_hash(payload)
        if current.capability == "learn" and current.status != "completed":
            learning_goal = (payload.learning_goal or "").strip()
            if not learning_goal:
                raise ValueError("learn confirmation requires learning_goal")
            create_input_hash = hashlib.sha256(learning_goal.encode("utf-8")).hexdigest()
        decision, replayed = service.confirm(
            decision_id,
            idempotency_key=payload.idempotency_key,
            confirmation_input_hash=create_input_hash,
        )
        if decision.capability == "learn":
            completed, action_replayed, pack_id, plan_id = _start_confirmed_learn(
                decision,
                payload,
                service=service,
            )
            return ConfirmRouteResponse(
                decision=_public(completed),
                replayed=replayed or action_replayed,
                pack_id=pack_id,
                plan_id=plan_id,
            )
        if decision.capability == "create":
            completed, action_replayed, generation_id = _start_confirmed_create(
                decision,
                payload,
                service=service,
                task_manager=task_manager,
            )
            return ConfirmRouteResponse(
                decision=_public(completed),
                replayed=replayed or action_replayed,
                generation_id=generation_id,
                events_url=f"/api/v1/traittutor/generate/tasks/{generation_id}/events",
                result_url=f"/api/v1/traittutor/generate/tasks/{generation_id}",
            )
        if decision.capability != "research":
            return ConfirmRouteResponse(decision=_public(decision), replayed=replayed)
        completed, action_replayed, workspace_id, brief_id, run_id = _start_confirmed_research(
            decision,
            payload,
            service=service,
            research_service=research_service,
        )
        if not action_replayed:
            background_tasks.add_task(research_scheduler.schedule, research_service, run_id)
        return ConfirmRouteResponse(
            decision=_public(completed),
            replayed=replayed or action_replayed,
            workspace_id=workspace_id,
            brief_id=brief_id,
            run_id=run_id,
        )
    except Exception as exc:
        if isinstance(exc, ResearchWorkspaceStoreError):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Research workspace unavailable",
            ) from exc
        _raise_store_error(exc)


__all__ = ["router"]
