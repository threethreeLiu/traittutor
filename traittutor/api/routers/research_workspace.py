"""Owner-bound REST API for durable Research Workspaces.

The composition root mounts this router at ``/api/v1``.  Product state comes
from :mod:`traittutor.research_workspace`; progress streams and provider
execution are deliberately outside this router.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field

from traittutor.gateway.service import get_gateway
from traittutor.generate.tasks import GenerationTaskManager, get_generation_task_manager
from traittutor.multi_user.context import get_current_user
from traittutor.multi_user.knowledge_access import resolve_for_rag
from traittutor.multi_user.models import CurrentUser
from traittutor.multi_user.paths import user_context
from traittutor.research_workspace.courseware import (
    ResearchCoursewareEvidenceError,
    ResearchCoursewareProvenance,
    prepare_research_courseware,
)
from traittutor.research_workspace.models import (
    ResearchBrief,
    ResearchClaim,
    ResearchContinuationRef,
    ResearchKnowledgeBaseBinding,
    ResearchNote,
    ResearchReportArtifact,
    ResearchRun,
    ResearchRunStatus,
    ResearchSource,
    ResearchWorkspace,
    ResearchWorkspaceStatus,
)
from traittutor.research_workspace.runtime import build_gateway_research_executor
from traittutor.research_workspace.scheduler import ResearchRunScheduler
from traittutor.research_workspace.service import ResearchWorkspaceService
from traittutor.research_workspace.state_machine import ResearchRunTransitionError
from traittutor.research_workspace.store import (
    ResearchWorkspaceIdempotencyConflict,
    ResearchWorkspaceStore,
    ResearchWorkspaceStoreError,
    ResearchWorkspaceVersionConflict,
)

router = APIRouter(prefix="/research/workspaces")


class CreateWorkspaceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str = Field(min_length=1, max_length=240)
    subject_id: str | None = Field(default=None, max_length=128)
    idempotency_key: str = Field(min_length=1, max_length=160)


class UpdateWorkspaceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    expected_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=160)
    title: str | None = Field(default=None, min_length=1, max_length=240)
    subject_id: str | None = Field(default=None, max_length=128)
    status: ResearchWorkspaceStatus | None = None


class SaveBriefRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    question: str = Field(min_length=1, max_length=12_000)
    objectives: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    constraints: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    source_policy: Literal["web", "knowledge_base", "mixed"] = "web"
    # This is resolved and frozen at save time.  A run accepts only brief id
    # and version, so it can never substitute an arbitrary client KB name.
    knowledge_base_ref: str | None = Field(default=None, min_length=1, max_length=384)
    expected_workspace_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=160)


class StartRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    brief_id: str = Field(min_length=1, max_length=96)
    brief_version: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=160)


class ContinueResearchRequest(SaveBriefRequest):
    """Create a versioned follow-up brief and queue its durable run."""

    parent_report_revision: int = Field(ge=1)


class CreateCoursewareFromResearchRequest(BaseModel):
    """Queue courseware only from the current, validated report evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    idempotency_key: str = Field(min_length=1, max_length=160)
    language: str | None = Field(default=None, min_length=2, max_length=32)


class RunLifecycleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    expected_revision: int = Field(ge=1)
    expected_status: ResearchRunStatus
    idempotency_key: str = Field(min_length=1, max_length=160)


class CreateNoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    body: str = Field(min_length=1, max_length=20_000)
    source_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=200)
    idempotency_key: str = Field(min_length=1, max_length=160)


class InvalidateSourceRequest(BaseModel):
    """CAS request for an audit-preserving evidence invalidation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    expected_revision: int = Field(ge=1)
    expected_status: Literal["active"]
    idempotency_key: str = Field(min_length=1, max_length=160)
    reason: str | None = Field(default=None, max_length=1_000)


class WorkspacePublic(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    workspace_id: str
    title: str
    subject_id: str | None
    status: ResearchWorkspaceStatus
    revision: int
    active_brief_id: str | None
    created_at: str
    updated_at: str


class KnowledgeBaseBindingPublic(BaseModel):
    """Path-free logical provenance for the KB frozen into a public brief."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    resource_id: str
    display_name: str
    source: Literal["admin", "user"]


class BriefPublic(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    brief_id: str
    workspace_id: str
    version: int
    question: str
    objectives: tuple[str, ...]
    constraints: tuple[str, ...]
    source_policy: Literal["web", "knowledge_base", "mixed"]
    knowledge_base: KnowledgeBaseBindingPublic | None
    continuation: ResearchContinuationRef | None
    created_at: str


class RunPublic(BaseModel):
    """Worker claims, provider inputs and idempotency material stay private."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    workspace_id: str
    brief_id: str
    brief_version: int
    status: ResearchRunStatus
    revision: int
    fencing_epoch: int
    failure_reason: str | None
    created_at: str
    updated_at: str


class SourcePublic(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str
    workspace_id: str
    url: AnyHttpUrl
    title: str
    excerpt: str | None
    retrieved_at: str
    revision: int
    status: Literal["active", "invalidated"]
    invalidated_at: str | None
    invalidation_reason: str | None


class NotePublic(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    note_id: str
    workspace_id: str
    body: str
    source_ids: tuple[str, ...]
    revision: int
    created_at: str
    updated_at: str


class ClaimPublic(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_id: str
    workspace_id: str
    run_id: str
    text: str
    kind: Literal["grounded", "inference"]
    source_ids: tuple[str, ...]
    created_at: str
    revision: int
    evidence_status: Literal["active", "needs_review"]
    review_required_source_ids: tuple[str, ...]


class ReportPublic(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    report_id: str
    workspace_id: str
    run_id: str
    body: str
    claims: tuple[ClaimPublic, ...]
    created_at: str
    revision: int
    evidence_status: Literal["active", "needs_review"]
    review_required_source_ids: tuple[str, ...]


class ResearchCoursewareSubmissionPublic(BaseModel):
    """A queue receipt with evidence identity, never report text or prompts."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    generation_id: str
    status: str
    research_run_id: str
    report_id: str
    report_revision: int
    source_ids: tuple[str, ...]
    result_url: str
    events_url: str
    # The browser receives only this safe receipt. The corresponding typed,
    # identity-only ref is frozen into ContextAssembler and PromptBundle by the
    # server composition root; report prose and source URLs never appear here.
    provenance_delivery: Literal["queue_snapshot_prompt_bundle_and_result_ref"]


ResearchServiceFactory = Callable[[CurrentUser], ResearchWorkspaceService]
ResearchSchedulerFactory = Callable[[CurrentUser], ResearchRunScheduler]
GenerationTaskManagerFactory = Callable[[], GenerationTaskManager]


def default_research_service_factory(user: CurrentUser) -> ResearchWorkspaceService:
    return ResearchWorkspaceService(ResearchWorkspaceStore(user.id))


research_service_factory: ResearchServiceFactory = default_research_service_factory


def default_research_scheduler_factory(user: CurrentUser) -> ResearchRunScheduler:
    """Build the production scheduler without exposing model configuration."""

    return ResearchRunScheduler(
        lambda: build_gateway_research_executor(user, gateway=get_gateway())
    )


research_scheduler_factory: ResearchSchedulerFactory = default_research_scheduler_factory
generation_task_manager_factory: GenerationTaskManagerFactory = get_generation_task_manager


def get_research_workspace_service() -> ResearchWorkspaceService:
    return research_service_factory(get_current_user())


def get_research_run_scheduler() -> ResearchRunScheduler:
    return research_scheduler_factory(get_current_user())


def get_generation_task_manager_for_research() -> GenerationTaskManager:
    """Keep the Research route on the normal owner-bound generation queue."""

    return generation_task_manager_factory()


ResearchService = Annotated[
    ResearchWorkspaceService,
    Depends(get_research_workspace_service),
]
ResearchScheduler = Annotated[
    ResearchRunScheduler,
    Depends(get_research_run_scheduler),
]
ResearchGenerationTaskManager = Annotated[
    GenerationTaskManager,
    Depends(get_generation_task_manager_for_research),
]


def _workspace_public(workspace: ResearchWorkspace) -> WorkspacePublic:
    return WorkspacePublic.model_validate(workspace.model_dump(mode="json", exclude={"owner_id"}))


def _brief_public(brief: ResearchBrief) -> BriefPublic:
    return BriefPublic(
        **brief.model_dump(
            mode="json",
            exclude={"owner_id", "content_hash", "knowledge_base"},
        ),
        knowledge_base=(
            KnowledgeBaseBindingPublic(
                resource_id=brief.knowledge_base.resource_id,
                display_name=brief.knowledge_base.display_name,
                source=brief.knowledge_base.source,
            )
            if brief.knowledge_base is not None
            else None
        ),
    )


def _freeze_kb_binding(
    request: SaveBriefRequest,
    *,
    user: CurrentUser,
) -> ResearchKnowledgeBaseBinding | None:
    if request.source_policy == "web":
        if request.knowledge_base_ref is not None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Web-only briefs cannot bind a knowledge base",
            )
        return None
    if request.knowledge_base_ref is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Knowledge-base and mixed briefs require a knowledge base",
        )
    with user_context(user):
        resource = resolve_for_rag(request.knowledge_base_ref)
    if resource is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge base not found"
        )
    return ResearchKnowledgeBaseBinding(
        resource_id=resource.id,
        display_name=resource.name,
        source=resource.source,
        authorized_owner_id=user.id,
    )


def _run_public(run: ResearchRun) -> RunPublic:
    return RunPublic.model_validate(
        run.model_dump(
            mode="json",
            exclude={
                "owner_id",
                "input_hash",
                "idempotency_key",
                "lease_revision",
                "claim_token",
                "claimed_by",
                "lease_expires_at",
            },
        )
    )


def _source_public(source: ResearchSource) -> SourcePublic:
    return SourcePublic.model_validate(source.model_dump(mode="json", exclude={"owner_id"}))


def _note_public(note: ResearchNote) -> NotePublic:
    return NotePublic.model_validate(note.model_dump(mode="json", exclude={"owner_id"}))


def _claim_public(claim: ResearchClaim) -> ClaimPublic:
    return ClaimPublic.model_validate(
        claim.model_dump(mode="json", exclude={"owner_id", "evidence_status_updated_at"})
    )


def _report_public(
    report: ResearchReportArtifact,
    claims: tuple[ResearchClaim, ...],
) -> ReportPublic:
    return ReportPublic(
        report_id=report.report_id,
        workspace_id=report.workspace_id,
        run_id=report.run_id,
        body=report.body,
        claims=tuple(
            _claim_public(claim) for claim in claims if claim.claim_id in report.claim_ids
        ),
        created_at=report.created_at,
        revision=report.revision,
        evidence_status=report.evidence_status,
        review_required_source_ids=report.review_required_source_ids,
    )


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Research object not found",
    )


def _map_domain_error(exc: Exception) -> HTTPException:
    if isinstance(exc, KeyError):
        return _not_found()
    if isinstance(exc, ResearchWorkspaceVersionConflict):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "revision_conflict",
                "expected_revision": exc.expected_revision,
                "actual_revision": exc.actual_revision,
            },
        )
    if isinstance(exc, ResearchWorkspaceIdempotencyConflict):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "idempotency_conflict"},
        )
    if isinstance(exc, ResearchRunTransitionError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "invalid_run_transition"},
        )
    if isinstance(exc, ResearchWorkspaceStoreError):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Research workspace unavailable",
        )
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=str(exc),
    )


def _workspace_or_404(
    service: ResearchWorkspaceService,
    workspace_id: str,
) -> ResearchWorkspace:
    workspace = service.get_workspace(workspace_id)
    if workspace is None:
        raise _not_found()
    return workspace


def _brief_or_404(
    service: ResearchWorkspaceService,
    workspace_id: str,
    brief_id: str,
    *,
    version: int | None = None,
) -> ResearchBrief:
    _workspace_or_404(service, workspace_id)
    brief = service.get_brief(brief_id, version=version)
    if brief is None or brief.workspace_id != workspace_id:
        raise _not_found()
    return brief


def _run_or_404(
    service: ResearchWorkspaceService,
    workspace_id: str,
    run_id: str,
) -> ResearchRun:
    _workspace_or_404(service, workspace_id)
    run = service.get_run(run_id)
    if run is None or run.workspace_id != workspace_id:
        raise _not_found()
    return run


def _source_or_404(
    service: ResearchWorkspaceService,
    workspace_id: str,
    source_id: str,
) -> ResearchSource:
    _workspace_or_404(service, workspace_id)
    source = next(
        (item for item in service.list_sources(workspace_id) if item.source_id == source_id),
        None,
    )
    if source is None:
        raise _not_found()
    return source


@router.get("", response_model=list[WorkspacePublic])
def list_workspaces(service: ResearchService) -> list[WorkspacePublic]:
    return [_workspace_public(workspace) for workspace in service.list_workspaces()]


@router.post("", response_model=WorkspacePublic, status_code=status.HTTP_201_CREATED)
def create_workspace(
    request: CreateWorkspaceRequest,
    service: ResearchService,
) -> WorkspacePublic:
    try:
        workspace = service.create_workspace(
            title=request.title,
            subject_id=request.subject_id,
            idempotency_key=request.idempotency_key,
        )
    except (KeyError, ValueError, ResearchWorkspaceStoreError) as exc:
        raise _map_domain_error(exc) from exc
    return _workspace_public(workspace)


@router.get("/{workspace_id}", response_model=WorkspacePublic)
def get_workspace(workspace_id: str, service: ResearchService) -> WorkspacePublic:
    return _workspace_public(_workspace_or_404(service, workspace_id))


@router.patch("/{workspace_id}", response_model=WorkspacePublic)
def update_workspace(
    workspace_id: str,
    request: UpdateWorkspaceRequest,
    service: ResearchService,
) -> WorkspacePublic:
    _workspace_or_404(service, workspace_id)
    try:
        workspace = service.update_workspace(
            workspace_id,
            expected_revision=request.expected_revision,
            idempotency_key=request.idempotency_key,
            title=request.title,
            subject_id=request.subject_id,
            status=request.status,
        )
    except (KeyError, ValueError, ResearchWorkspaceStoreError) as exc:
        raise _map_domain_error(exc) from exc
    return _workspace_public(workspace)


@router.get("/{workspace_id}/briefs", response_model=list[BriefPublic])
def list_briefs(workspace_id: str, service: ResearchService) -> list[BriefPublic]:
    _workspace_or_404(service, workspace_id)
    return [_brief_public(brief) for brief in service.list_briefs(workspace_id)]


@router.post(
    "/{workspace_id}/briefs",
    response_model=BriefPublic,
    status_code=status.HTTP_201_CREATED,
)
def create_brief(
    workspace_id: str,
    request: SaveBriefRequest,
    service: ResearchService,
    user: Annotated[CurrentUser, Depends(get_current_user)],
) -> BriefPublic:
    _workspace_or_404(service, workspace_id)
    try:
        brief = service.save_brief(
            workspace_id,
            question=request.question,
            objectives=request.objectives,
            constraints=request.constraints,
            source_policy=request.source_policy,
            knowledge_base=_freeze_kb_binding(request, user=user),
            expected_workspace_revision=request.expected_workspace_revision,
            idempotency_key=request.idempotency_key,
        )
    except (KeyError, ValueError, ResearchWorkspaceStoreError) as exc:
        raise _map_domain_error(exc) from exc
    return _brief_public(brief)


@router.get("/{workspace_id}/briefs/{brief_id}", response_model=BriefPublic)
def get_brief(
    workspace_id: str,
    brief_id: str,
    service: ResearchService,
    version: Annotated[int | None, Query(ge=1)] = None,
) -> BriefPublic:
    return _brief_public(_brief_or_404(service, workspace_id, brief_id, version=version))


@router.put("/{workspace_id}/briefs/{brief_id}", response_model=BriefPublic)
def update_brief(
    workspace_id: str,
    brief_id: str,
    request: SaveBriefRequest,
    service: ResearchService,
    user: Annotated[CurrentUser, Depends(get_current_user)],
) -> BriefPublic:
    workspace = _workspace_or_404(service, workspace_id)
    if workspace.active_brief_id != brief_id:
        raise _not_found()
    return create_brief(workspace_id, request, service, user)


@router.get("/{workspace_id}/runs", response_model=list[RunPublic])
def list_runs(workspace_id: str, service: ResearchService) -> list[RunPublic]:
    _workspace_or_404(service, workspace_id)
    return [_run_public(run) for run in service.list_runs(workspace_id)]


@router.post(
    "/{workspace_id}/runs",
    response_model=RunPublic,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_run(
    workspace_id: str,
    request: StartRunRequest,
    background_tasks: BackgroundTasks,
    service: ResearchService,
    scheduler: ResearchScheduler,
) -> RunPublic:
    _workspace_or_404(service, workspace_id)
    try:
        run = service.start_run(
            workspace_id,
            brief_id=request.brief_id,
            brief_version=request.brief_version,
            idempotency_key=request.idempotency_key,
        )
    except (KeyError, ValueError, ResearchWorkspaceStoreError) as exc:
        raise _map_domain_error(exc) from exc
    background_tasks.add_task(scheduler.schedule, service, run.run_id)
    return _run_public(run)


@router.post(
    "/{workspace_id}/runs/{run_id}/follow-up",
    response_model=RunPublic,
    status_code=status.HTTP_202_ACCEPTED,
)
def continue_research(
    workspace_id: str,
    run_id: str,
    request: ContinueResearchRequest,
    background_tasks: BackgroundTasks,
    service: ResearchService,
    scheduler: ResearchScheduler,
    user: Annotated[CurrentUser, Depends(get_current_user)],
) -> RunPublic:
    """Start a fresh run from one exact, still-active report revision."""

    parent = _run_or_404(service, workspace_id, run_id)
    if parent.status != "completed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "follow_up_requires_completed_run"},
        )
    report = service.get_report(run_id)
    if (
        report is None
        or report.revision != request.parent_report_revision
        or report.evidence_status != "active"
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "follow_up_report_changed_or_unavailable"},
        )
    try:
        brief = service.save_brief(
            workspace_id,
            question=request.question,
            objectives=request.objectives,
            constraints=request.constraints,
            source_policy=request.source_policy,
            knowledge_base=_freeze_kb_binding(request, user=user),
            continuation=ResearchContinuationRef(
                parent_run_id=run_id,
                report_id=report.report_id,
                report_revision=report.revision,
            ),
            expected_workspace_revision=request.expected_workspace_revision,
            idempotency_key=f"{request.idempotency_key}:brief",
        )
        follow_up = service.start_run(
            workspace_id,
            brief_id=brief.brief_id,
            brief_version=brief.version,
            idempotency_key=f"{request.idempotency_key}:run",
        )
    except (KeyError, ValueError, ResearchWorkspaceStoreError) as exc:
        raise _map_domain_error(exc) from exc
    background_tasks.add_task(scheduler.schedule, service, follow_up.run_id)
    return _run_public(follow_up)


@router.get("/{workspace_id}/runs/{run_id}", response_model=RunPublic)
def get_run(
    workspace_id: str,
    run_id: str,
    service: ResearchService,
) -> RunPublic:
    return _run_public(_run_or_404(service, workspace_id, run_id))


@router.post(
    "/{workspace_id}/runs/{run_id}/courseware",
    response_model=ResearchCoursewareSubmissionPublic,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_courseware_from_research(
    workspace_id: str,
    run_id: str,
    request: CreateCoursewareFromResearchRequest,
    service: ResearchService,
    task_manager: ResearchGenerationTaskManager,
) -> ResearchCoursewareSubmissionPublic:
    """Queue learning content from an active, completed research evidence set.

    The report body, source references, run id and revisions are constructed
    server-side.  Replays use a deterministic generation identity, while the
    standard generation task worker rechecks the same evidence immediately
    before and after provider execution so a late source invalidation cannot
    publish a new artifact from stale material.
    """

    _run_or_404(service, workspace_id, run_id)
    try:
        prepared = prepare_research_courseware(
            service,
            workspace_id=workspace_id,
            run_id=run_id,
            idempotency_key=request.idempotency_key,
            language=request.language,
        )
        task = task_manager.create_idempotent(
            prepared.request,
            generation_id=prepared.generation_id,
        )
    except (ResearchCoursewareEvidenceError, ValueError, ResearchWorkspaceStoreError) as exc:
        raise _map_domain_error(exc) from exc
    provenance: ResearchCoursewareProvenance = prepared.provenance
    return ResearchCoursewareSubmissionPublic(
        generation_id=task.generation_id,
        status=task.status,
        research_run_id=provenance.research_run_id,
        report_id=provenance.report_id,
        report_revision=provenance.report_revision,
        source_ids=tuple(source.source_id for source in provenance.source_refs),
        result_url=f"/api/v1/traittutor/generate/tasks/{task.generation_id}",
        events_url=f"/api/v1/traittutor/generate/tasks/{task.generation_id}/events",
        provenance_delivery="queue_snapshot_prompt_bundle_and_result_ref",
    )


def _transition_action(
    service: ResearchWorkspaceService,
    workspace_id: str,
    run_id: str,
    request: RunLifecycleRequest,
    *,
    action: Literal["pause", "resume", "cancel", "retry"],
) -> tuple[RunPublic, bool]:
    """Apply a CAS lifecycle action and report whether it newly queued work.

    An idempotency replay returns the historical transition result, but must
    not enqueue the same durable run again.  Fencing would reject a duplicate
    claim eventually; avoiding the redundant background task makes the API
    behavior itself idempotent as well.
    """
    run = _run_or_404(service, workspace_id, run_id)
    newly_applied = run.revision == request.expected_revision
    if run.revision == request.expected_revision and run.status != request.expected_status:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "run_state_conflict", "actual_status": run.status},
        )
    targets: dict[str, dict[ResearchRunStatus, ResearchRunStatus]] = {
        "pause": {"queued": "paused", "running": "pausing"},
        "resume": {"paused": "queued"},
        "retry": {"failed": "queued", "needs_review": "queued"},
        "cancel": {
            "draft": "cancelled",
            "queued": "cancelled",
            "running": "cancelling",
            "pausing": "cancelling",
            "paused": "cancelled",
            "cancelling": "cancelled",
        },
    }
    target = targets[action].get(request.expected_status)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "invalid_run_transition"},
        )
    try:
        transitioned = service.transition_run(
            run_id,
            target,
            expected_revision=request.expected_revision,
            idempotency_key=request.idempotency_key,
        )
    except (KeyError, ValueError, ResearchWorkspaceStoreError) as exc:
        raise _map_domain_error(exc) from exc
    return _run_public(transitioned), newly_applied


@router.post("/{workspace_id}/runs/{run_id}/pause", response_model=RunPublic)
def pause_run(
    workspace_id: str,
    run_id: str,
    request: RunLifecycleRequest,
    service: ResearchService,
) -> RunPublic:
    transitioned, _ = _transition_action(service, workspace_id, run_id, request, action="pause")
    return transitioned


@router.post("/{workspace_id}/runs/{run_id}/resume", response_model=RunPublic)
def resume_run(
    workspace_id: str,
    run_id: str,
    request: RunLifecycleRequest,
    background_tasks: BackgroundTasks,
    service: ResearchService,
    scheduler: ResearchScheduler,
) -> RunPublic:
    transitioned, newly_queued = _transition_action(
        service, workspace_id, run_id, request, action="resume"
    )
    if newly_queued:
        background_tasks.add_task(scheduler.schedule, service, run_id)
    return transitioned


@router.post("/{workspace_id}/runs/{run_id}/retry", response_model=RunPublic)
def retry_run(
    workspace_id: str,
    run_id: str,
    request: RunLifecycleRequest,
    background_tasks: BackgroundTasks,
    service: ResearchService,
    scheduler: ResearchScheduler,
) -> RunPublic:
    """Retry only a failed or explicitly reviewable frozen run.

    The original brief remains immutable.  The state-store transition advances
    the fencing epoch before this background task can claim the new attempt.
    """
    transitioned, newly_queued = _transition_action(
        service, workspace_id, run_id, request, action="retry"
    )
    if newly_queued:
        background_tasks.add_task(scheduler.schedule, service, run_id)
    return transitioned


@router.post("/{workspace_id}/runs/{run_id}/cancel", response_model=RunPublic)
def cancel_run(
    workspace_id: str,
    run_id: str,
    request: RunLifecycleRequest,
    service: ResearchService,
) -> RunPublic:
    transitioned, _ = _transition_action(service, workspace_id, run_id, request, action="cancel")
    return transitioned


@router.get("/{workspace_id}/sources", response_model=list[SourcePublic])
def list_sources(workspace_id: str, service: ResearchService) -> list[SourcePublic]:
    _workspace_or_404(service, workspace_id)
    return [_source_public(source) for source in service.list_sources(workspace_id)]


@router.delete("/{workspace_id}/sources/{source_id}", response_model=SourcePublic)
def invalidate_source(
    workspace_id: str,
    source_id: str,
    request: InvalidateSourceRequest,
    service: ResearchService,
) -> SourcePublic:
    """Invalidate a source and atomically surface dependent review work.

    The source, cited claim text and report body are retained; only their
    evidence status changes.  Owner/workspace scope is resolved before the
    mutation and the store repeats that check under its file lock.
    """

    _source_or_404(service, workspace_id, source_id)
    try:
        invalidated = service.invalidate_source(
            workspace_id,
            source_id,
            expected_revision=request.expected_revision,
            idempotency_key=request.idempotency_key,
            reason=request.reason,
        )
    except (KeyError, ValueError, ResearchWorkspaceStoreError) as exc:
        raise _map_domain_error(exc) from exc
    return _source_public(invalidated)


@router.get("/{workspace_id}/notes", response_model=list[NotePublic])
def list_notes(workspace_id: str, service: ResearchService) -> list[NotePublic]:
    _workspace_or_404(service, workspace_id)
    return [_note_public(note) for note in service.list_notes(workspace_id)]


@router.post(
    "/{workspace_id}/notes",
    response_model=NotePublic,
    status_code=status.HTTP_201_CREATED,
)
def create_note(
    workspace_id: str,
    request: CreateNoteRequest,
    service: ResearchService,
) -> NotePublic:
    _workspace_or_404(service, workspace_id)
    try:
        note = service.create_note(
            workspace_id,
            body=request.body,
            source_ids=request.source_ids,
            idempotency_key=request.idempotency_key,
        )
    except (KeyError, ValueError, ResearchWorkspaceStoreError) as exc:
        raise _map_domain_error(exc) from exc
    return _note_public(note)


@router.get("/{workspace_id}/notes/{note_id}", response_model=NotePublic)
def get_note(
    workspace_id: str,
    note_id: str,
    service: ResearchService,
) -> NotePublic:
    _workspace_or_404(service, workspace_id)
    note = next(
        (item for item in service.list_notes(workspace_id) if item.note_id == note_id),
        None,
    )
    if note is None:
        raise _not_found()
    return _note_public(note)


@router.get("/{workspace_id}/runs/{run_id}/report", response_model=ReportPublic)
def get_report(
    workspace_id: str,
    run_id: str,
    service: ResearchService,
) -> ReportPublic:
    _run_or_404(service, workspace_id, run_id)
    report = service.get_report(run_id)
    if report is None:
        raise _not_found()
    return _report_public(report, service.list_claims(run_id))


__all__ = [
    "default_research_scheduler_factory",
    "default_research_service_factory",
    "get_research_run_scheduler",
    "get_research_workspace_service",
    "research_scheduler_factory",
    "research_service_factory",
    "router",
]
