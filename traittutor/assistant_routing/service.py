"""Deterministic, confirmation-gated assistant capability routing."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import hashlib
import json
from typing import Literal
from urllib.parse import urlsplit
from uuid import uuid4

from traittutor.learning.intent import scan_untrusted_learning_payload

from .models import Capability, CapabilityDecision, SearchReceipt, SearchSourceRef, utc_now
from .store import CapabilityDecisionStore

SearchExecutor = Callable[[str], Awaitable[dict[str, object]]]

_CONFIRMATION_CAPABILITIES: frozenset[Capability] = frozenset({"research", "learn", "create"})
_SEARCH_LOCKS: dict[tuple[str, str], asyncio.Lock] = {}
_MARKERS: tuple[tuple[Capability, tuple[str, ...]], ...] = (
    ("research", ("deep research", "research", "研究", "调研", "查文献")),
    ("create", ("create course", "create a course", "generate page", "生成课件", "生成学习页")),
    ("learn", ("teach me", "help me learn", "study ", "learn ", "练习", "测验", "学习", "教我")),
    ("search", ("search", "look up", "find online", "latest", "联网", "搜索", "查一下")),
)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _route_hash(
    *, message_digest: str, session_id: str | None, requested_capability: Capability | None
) -> str:
    canonical = json.dumps(
        {
            "message_digest": message_digest,
            "session_id": session_id or "",
            "requested_capability": requested_capability,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    return _digest(canonical)


def classify_capability(message: str) -> Capability:
    """Classify with bounded deterministic markers; absence means chat."""
    normalized = message.casefold()
    for capability, markers in _MARKERS:
        if any(marker in normalized for marker in markers):
            return capability
    return "chat"


def _action_target(capability: Capability, *, session_id: str | None) -> dict[str, object]:
    """Return a typed target, never an assertion that a costly action ran."""
    targets: dict[Capability, dict[str, object]] = {
        # Ordinary chat continues the in-progress unified turn over the
        # existing /api/v1/ws; the decision never advertises a transport
        # address, so a client cannot self-select a WebSocket URL.
        "chat": {"kind": "continue_unified_turn"},
        "search": {"kind": "builtin_tool", "tool_name": "web_search"},
        "research": {
            "kind": "research_workspace_intent",
            "path": "/api/v1/research/workspaces",
            "execution": "not_started",
        },
        "learn": {
            "kind": "learning_pack_intent",
            "path": "/api/v1/learning-packs",
            "execution": "not_started",
        },
        "create": {
            "kind": "courseware_intent",
            "path": "/api/v1/traittutor/generate",
            "execution": "not_started",
        },
    }
    target = dict(targets[capability])
    if session_id:
        target["session_id"] = session_id
    return target


def _search_receipt(raw: dict[str, object]) -> SearchReceipt:
    """Mint source refs only from valid citations returned by the search tool."""
    raw_sources = raw.get("sources")
    sources: list[SearchSourceRef] = []
    seen_urls: set[str] = set()
    if isinstance(raw_sources, list):
        for source in raw_sources[:12]:
            if not isinstance(source, dict):
                continue
            url = str(source.get("url") or "").strip()
            if url in seen_urls:
                continue
            hostname = urlsplit(url).hostname or "Web source"
            try:
                candidate = SearchSourceRef(
                    source_id=f"web-{_digest(url)[:16]}",
                    reference=f"[S{len(sources) + 1}]",
                    title=(str(source.get("title") or "").strip() or hostname)[:240],
                    url=url,
                    snippet=str(source.get("snippet") or "")[:1_000],
                )
            except ValueError:
                continue
            seen_urls.add(url)
            sources.append(candidate)
    if not sources:
        # A provider/model statement is never promoted to evidence merely
        # because it resembles a URL. Without tool-returned citations there
        # is no source-backed answer to inject into the learner's thread.
        return SearchReceipt(
            status="unavailable",
            content="Search completed, but no verifiable web sources were returned.",
            degradation_code="no_citable_sources",
        )
    content = str(raw.get("content") or "").strip()[:12_000]
    return SearchReceipt(
        status="ready",
        content=content or "Search completed. Review the verified sources below.",
        sources=tuple(sources),
        source_refs=tuple(source.source_id for source in sources),
    )


class CapabilityRoutingService:
    """Route first, then require explicit confirmation before costly hand-offs.

    The service does not create a LearningPack, generation task, memory item,
    or learning event.  Research execution is composed by the authenticated
    API router after this service records consent, preserving the Research
    Workspace as the owner of durable research resources.
    """

    def __init__(self, store: CapabilityDecisionStore, *, search_executor: SearchExecutor) -> None:
        self._store = store
        self._search_executor = search_executor

    async def route(
        self,
        *,
        message: str,
        session_id: str | None,
        requested_capability: Capability | None,
        idempotency_key: str,
    ) -> tuple[CapabilityDecision | None, bool, dict[str, object] | None]:
        """Scan first; risky input creates no decision and triggers no tool."""
        action, category = scan_untrusted_learning_payload({"message": message})
        if action != "allow":
            return None, False, {"code": "unsafe_input", "category": category or "unsafe_input"}

        normalized_message = message.strip()
        digest = _digest(normalized_message)
        automatic = classify_capability(normalized_message)
        selected = requested_capability or automatic
        manual_override = requested_capability is not None
        needs_confirmation = selected in _CONFIRMATION_CAPABILITIES
        status: Literal["ready", "confirmation_required", "completed"] = (
            "confirmation_required"
            if needs_confirmation
            else "ready"
            if selected == "search"
            else "completed"
        )
        now = utc_now()
        decision = CapabilityDecision(
            decision_id=f"cap_{uuid4().hex[:20]}",
            owner_id=self._store.owner_id,
            message_digest=digest,
            session_id=session_id,
            capability=selected,
            requested_capability=requested_capability,
            manual_override=manual_override,
            status=status,
            requires_confirmation=needs_confirmation,
            action_target=_action_target(selected, session_id=session_id),
            reason="manual capability selection"
            if manual_override
            else "deterministic intent classification",
            revision=1,
            created_at=now,
            updated_at=now,
        )
        stored, replayed = self._store.create_or_replay(
            decision,
            idempotency_key=idempotency_key,
            request_hash=_route_hash(
                message_digest=digest,
                session_id=session_id,
                requested_capability=requested_capability,
            ),
        )
        if stored.capability != "search" or stored.status != "ready":
            return stored, replayed, None

        # The per-decision lock prevents concurrent duplicate calls before the
        # first worker can replace the ready decision with its final outcome.
        lock_key = (self._store.owner_id, stored.decision_id)
        lock = _SEARCH_LOCKS.setdefault(lock_key, asyncio.Lock())
        async with lock:
            current = self._store.get(stored.decision_id)
            if current.status != "ready":
                return current, True, None
            try:
                receipt = _search_receipt(await self._search_executor(normalized_message))
            except Exception:
                receipt = SearchReceipt(
                    status="unavailable",
                    content="Live web search is temporarily unavailable. No sources were created.",
                    degradation_code="search_unavailable",
                )
                failure_reason = "web search unavailable"
            else:
                failure_reason = (
                    "web search returned no citable sources"
                    if receipt.status == "unavailable"
                    else current.reason
                )
            completed = current.model_copy(
                update={
                    "status": "completed" if receipt.status == "ready" else "failed",
                    "search_receipt": receipt,
                    "reason": failure_reason,
                    "revision": current.revision + 1,
                    "updated_at": utc_now(),
                }
            )
            saved = self._store.replace(completed)
            return saved, replayed, None

    def record_search_delivery(
        self,
        decision_id: str,
        *,
        session_id: str,
        user_message_id: int,
        message_id: int,
    ) -> tuple[CapabilityDecision, bool]:
        """Attach one server-written thread receipt to the durable decision."""
        current = self._store.get(decision_id)
        receipt = current.search_receipt
        if current.capability != "search" or receipt is None:
            raise ValueError("search decision has no executable receipt")
        if receipt.message_id is not None:
            return current, True
        delivered = current.model_copy(
            update={
                "session_id": session_id,
                "action_target": {
                    **current.action_target,
                    "session_id": session_id,
                    "execution": "delivered",
                },
                "search_receipt": receipt.model_copy(
                    update={
                        "session_id": session_id,
                        "user_message_id": user_message_id,
                        "message_id": message_id,
                    }
                ),
                "revision": current.revision + 1,
                "updated_at": utc_now(),
            }
        )
        return self._store.replace(delivered), False

    def bind_search_session(
        self,
        decision_id: str,
        *,
        session_id: str,
    ) -> tuple[CapabilityDecision, bool]:
        """Persist the canonical thread before writing either Search message."""
        current = self._store.get(decision_id)
        if current.capability != "search" or current.search_receipt is None:
            raise ValueError("search decision has no executable receipt")
        if current.session_id is not None:
            if current.session_id != session_id:
                raise ValueError("search decision is already bound to another session")
            return current, True
        bound = current.model_copy(
            update={
                "session_id": session_id,
                "action_target": {**current.action_target, "session_id": session_id},
                "search_receipt": current.search_receipt.model_copy(
                    update={"session_id": session_id}
                ),
                "revision": current.revision + 1,
                "updated_at": utc_now(),
            }
        )
        return self._store.replace(bound), False

    def confirm(
        self,
        decision_id: str,
        *,
        idempotency_key: str,
        confirmation_input_hash: str | None = None,
    ) -> tuple[CapabilityDecision, bool]:
        """Record consent exactly once; resource owners perform the action."""
        current = self._store.get(decision_id)
        if current.capability == "create":
            if not confirmation_input_hash and current.status != "completed":
                raise ValueError("create confirmation requires a re-scanned input hash")
            if (
                confirmation_input_hash is not None
                and current.confirmation_input_hash is not None
                and current.confirmation_input_hash != confirmation_input_hash
            ):
                raise ValueError("create confirmation input does not match the accepted contract")
        confirmed = current.model_copy(
            update={
                "status": "confirmed",
                "requires_confirmation": False,
                "reason": "user confirmed capability hand-off",
                "confirmation_input_hash": confirmation_input_hash
                if current.capability == "create"
                else current.confirmation_input_hash,
                "revision": current.revision + 1,
                "updated_at": utc_now(),
            }
        )
        return self._store.confirm_or_replay(
            decision_id,
            idempotency_key=idempotency_key,
            request_hash=_digest("confirm.v1"),
            confirmed=confirmed,
        )

    def get(self, decision_id: str) -> CapabilityDecision:
        """Read the current owner-bound decision before an input-bearing action."""
        return self._store.get(decision_id)

    def complete_research_action(
        self,
        decision_id: str,
        *,
        workspace_id: str,
        brief_id: str,
        run_id: str,
    ) -> tuple[CapabilityDecision, bool]:
        """Record the one scheduler hand-off after Research Workspace writes.

        This receipt intentionally contains only public resource identifiers,
        never the submitted research question or a provider prompt.
        """
        return self._store.complete_research_action(
            decision_id,
            action_target={
                "kind": "research_workspace_run",
                "path": f"/api/v1/research/workspaces/{workspace_id}/runs/{run_id}",
                "execution": "scheduled",
                "workspace_id": workspace_id,
                "brief_id": brief_id,
                "run_id": run_id,
            },
        )

    def complete_learn_action(
        self,
        decision_id: str,
        *,
        pack_id: str,
        plan_id: str,
    ) -> tuple[CapabilityDecision, bool]:
        """Record the one owner-local Pack and plan created after consent."""
        return self._store.complete_learn_action(
            decision_id,
            action_target={
                "kind": "learning_pack_plan",
                "path": f"/learning/{pack_id}",
                "execution": "created",
                "pack_id": pack_id,
                "plan_id": plan_id,
            },
        )

    def complete_create_action(
        self,
        decision_id: str,
        *,
        generation_id: str,
    ) -> tuple[CapabilityDecision, bool]:
        """Record the one queued task after its strict input contract passed.

        The decision ledger intentionally records only the opaque task handle;
        the re-scanned goal and material remain in the owner-bound generation
        task record rather than becoming a second routing transcript.
        """
        return self._store.complete_create_action(
            decision_id,
            action_target={
                "kind": "courseware_generation_task",
                "path": f"/api/v1/traittutor/generate/tasks/{generation_id}",
                "execution": "queued",
                "generation_id": generation_id,
            },
        )


__all__ = ["CapabilityRoutingService", "SearchExecutor", "classify_capability"]
