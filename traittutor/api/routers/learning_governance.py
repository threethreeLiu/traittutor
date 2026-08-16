"""Learner-safe, owner-bound Learning Governance read API.

Mounted by the application composition root at ``/api/v1``. Authentication is
required at router registration, exactly like the adjacent learning routers;
the handlers derive owner identity exclusively from the request ContextVar.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from traittutor.learning.service import LearningService
from traittutor.learning_governance.models import (
    ErrorSummary,
    LearnerEventAmendmentReceipt,
    LearnerSubjectLearningState,
    MisconceptionSummary,
    RepairSummary,
    ReviewSummary,
    VoidLearnerEventRequest,
)
from traittutor.learning_governance.runtime import (
    GovernanceStoreBundle,
    build_governance_repository,
    default_governance_store_bundle,
)
from traittutor.learning_governance.service import LearningGovernanceService
from traittutor.learning_model.events import (
    LearnerEventAmendment,
    stable_amendment_identity,
)
from traittutor.multi_user.context import get_current_user
from traittutor.multi_user.models import CurrentUser
from traittutor.multi_user.paths import get_path_service_for_scope

router = APIRouter()
logger = logging.getLogger(__name__)

SubjectQuery = Annotated[str, Query(min_length=1, max_length=96)]
KcQuery = Annotated[str | None, Query(min_length=1, max_length=96)]


GovernanceStoreFactory = Callable[[CurrentUser], GovernanceStoreBundle]


def default_governance_store_factory(user: CurrentUser) -> GovernanceStoreBundle:
    """Open only the current request owner's workspace-backed stores."""
    return default_governance_store_bundle(
        user,
        path_service_resolver=get_path_service_for_scope,
    )


# Tests replace this narrow factory, while production always uses the
# authenticated request context installed by require_auth.
governance_store_factory: GovernanceStoreFactory = default_governance_store_factory


def get_learning_governance_service() -> LearningGovernanceService:
    user = get_current_user()
    stores = governance_store_factory(user)
    repository = build_governance_repository(user, stores=stores)
    return LearningGovernanceService(repository)


GovernanceService = Annotated[
    LearningGovernanceService,
    Depends(get_learning_governance_service),
]


def _mastery_path_ready(progress: object) -> bool:
    """Whether a persisted path can be selected for Mastery Chat.

    The browser receives only this coarse availability bit, never the path's
    subject or KC graph.  The turn runtime still re-loads and fingerprints the
    full binding before mounting tools, so this list is convenience UI rather
    than an authorization decision.
    """
    subject_id = str(getattr(progress, "subject_id", "") or "").strip()
    modules = getattr(progress, "modules", []) or []
    kc_ids = [
        str(getattr(kp, "id", "") or "").strip()
        for module in modules
        for kp in (getattr(module, "knowledge_points", []) or [])
    ]
    return bool(subject_id and kc_ids and all(kc_ids) and len(kc_ids) == len(set(kc_ids)))


@router.get("/learning/progress")
async def list_learning_progress_paths() -> dict[str, object]:
    """List only the authenticated owner's persisted learning paths.

    This replaces the stale unscoped progress-list client contract.  It uses
    the existing ``LearningService.list_progress`` projection over an
    owner-scoped store, then adds only a boolean indicating whether the path
    has the server-authored subject/KC data needed by Mastery Chat.
    """
    stores = governance_store_factory(get_current_user())
    listed = LearningService(
        stores.learning_store,
        resume_canonical_derivations=False,
    ).list_progress()
    summaries = listed.get("summaries", [])
    if not isinstance(summaries, list):
        return {"summaries": [], "errors": [{"book_id": "", "error": "Invalid progress list"}]}
    safe_summaries: list[dict[str, object]] = []
    for summary in summaries:
        if not isinstance(summary, dict):
            continue
        book_id = str(summary.get("book_id") or "").strip()
        if not book_id:
            continue
        try:
            progress = stores.learning_store.load(book_id)
        except (OSError, ValueError):
            # The underlying service already omits malformed progress from its
            # summary list.  A concurrent deletion also means there is no safe
            # selectable target, so fail closed rather than returning a stale id.
            continue
        if progress is None or progress.book_id != book_id:
            continue
        safe_summaries.append(
            {
                "book_id": book_id,
                "name": str(summary.get("name") or book_id),
                "modules_count": int(summary.get("modules_count") or 0),
                "kp_count": int(summary.get("kp_count") or 0),
                "current_stage": str(summary.get("current_stage") or ""),
                "evidence_state_counts": dict(summary.get("evidence_state_counts") or {}),
                "updated_at": float(summary.get("updated_at") or 0),
                "mastery_ready": _mastery_path_ready(progress),
            }
        )
    errors = listed.get("errors", [])
    return {
        "summaries": safe_summaries,
        "errors": errors if isinstance(errors, list) else [],
    }


def _snapshot(
    service: LearningGovernanceService,
    *,
    subject_id: str,
    kc_id: str | None,
):
    try:
        return service.snapshot(subject_id=subject_id, kc_id=kc_id)
    except PermissionError as exc:
        # Ownership failures are indistinguishable from absence. Returning a
        # validation error would disclose that a foreign partition exists.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found") from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc


@router.get("/learning-state", response_model=LearnerSubjectLearningState)
async def get_subject_learning_state(
    subject_id: SubjectQuery,
    service: GovernanceService,
) -> LearnerSubjectLearningState:
    """Serve the authenticated owner's canonical BKT evidence for one subject.

    The immutable ledger snapshot contains an internal owner id so projection
    code can enforce its partition.  Never send that id, an answer, a rubric,
    or an uncalibrated posterior to the browser.
    """
    try:
        snapshot = service.subject_learning_state_snapshot(subject_id=subject_id)
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found") from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    return LearnerSubjectLearningState.model_validate(snapshot.model_dump(exclude={"owner_id"}))


@router.get("/errors", response_model=list[ErrorSummary])
async def list_errors(
    subject_id: SubjectQuery,
    service: GovernanceService,
    kc_id: KcQuery = None,
) -> list[ErrorSummary]:
    return list(_snapshot(service, subject_id=subject_id, kc_id=kc_id).errors)


@router.get("/errors/{error_id}", response_model=ErrorSummary)
async def get_error(
    error_id: str,
    subject_id: SubjectQuery,
    service: GovernanceService,
    kc_id: KcQuery = None,
) -> ErrorSummary:
    snapshot = _snapshot(service, subject_id=subject_id, kc_id=kc_id)
    item = next((item for item in snapshot.errors if item.error_id == error_id), None)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Error not found")
    return item


@router.get("/repairs", response_model=list[RepairSummary])
async def list_repairs(
    subject_id: SubjectQuery,
    service: GovernanceService,
    kc_id: KcQuery = None,
) -> list[RepairSummary]:
    return list(_snapshot(service, subject_id=subject_id, kc_id=kc_id).repairs)


@router.get("/errors/{error_id}/repairs", response_model=list[RepairSummary])
async def list_error_repairs(
    error_id: str,
    subject_id: SubjectQuery,
    service: GovernanceService,
    kc_id: KcQuery = None,
) -> list[RepairSummary]:
    snapshot = _snapshot(service, subject_id=subject_id, kc_id=kc_id)
    if not any(item.error_id == error_id for item in snapshot.errors):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Error not found")
    return [item for item in snapshot.repairs if item.error_id == error_id]


@router.get("/misconceptions", response_model=list[MisconceptionSummary])
async def list_misconceptions(
    subject_id: SubjectQuery,
    service: GovernanceService,
    kc_id: KcQuery = None,
) -> list[MisconceptionSummary]:
    return list(_snapshot(service, subject_id=subject_id, kc_id=kc_id).misconceptions)


@router.get("/misconceptions/{hypothesis_id}", response_model=MisconceptionSummary)
async def get_misconception(
    hypothesis_id: str,
    subject_id: SubjectQuery,
    service: GovernanceService,
    kc_id: KcQuery = None,
) -> MisconceptionSummary:
    snapshot = _snapshot(service, subject_id=subject_id, kc_id=kc_id)
    item = next(
        (item for item in snapshot.misconceptions if item.hypothesis_id == hypothesis_id),
        None,
    )
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Misconception not found")
    return item


@router.get("/reviews", response_model=list[ReviewSummary])
async def list_reviews(
    subject_id: SubjectQuery,
    service: GovernanceService,
    kc_id: KcQuery = None,
) -> list[ReviewSummary]:
    return list(_snapshot(service, subject_id=subject_id, kc_id=kc_id).reviews)


@router.get("/reviews/{review_id}", response_model=ReviewSummary)
async def get_review(
    review_id: str,
    subject_id: SubjectQuery,
    service: GovernanceService,
    kc_id: KcQuery = None,
) -> ReviewSummary:
    snapshot = _snapshot(service, subject_id=subject_id, kc_id=kc_id)
    item = next((item for item in snapshot.reviews if item.review_id == review_id), None)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review not found")
    return item


@router.post(
    "/learner-events/{event_id}/void",
    response_model=LearnerEventAmendmentReceipt,
    status_code=status.HTTP_201_CREATED,
)
async def void_learner_event(
    event_id: str,
    body: VoidLearnerEventRequest,
) -> LearnerEventAmendmentReceipt:
    """Append a server-targeted void and retract an already-live BKT signal.

    The route intentionally does not accept user/KC/answer values.  It first
    resolves the immutable event from this authenticated owner's ledger, then
    copies its partition into the amendment under the ledger lock.  A retry
    reuses the same target-bound identity and cannot create a second void.
    """
    user = get_current_user()
    stores = governance_store_factory(user)
    target = stores.event_ledger.get(event_id)
    requested_subject = body.subject_id.strip()
    if target is None or target.user_id != user.id or target.subject_id != requested_subject:
        # Do not reveal another owner/subject's event existence.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")

    amendment_id, idempotency_key = stable_amendment_identity(
        user_id=user.id,
        target_event_id=target.event_id,
    )
    amendment = LearnerEventAmendment(
        amendment_id=amendment_id,
        idempotency_key=idempotency_key,
        target_event_id=target.event_id,
        user_id=target.user_id,
        subject_id=target.subject_id,
        kc_ids=target.kc_ids,
        reason_code=body.reason_code,
        created_at=datetime.now(UTC).isoformat(),
    )
    reconciliation_operation = stores.event_ledger.amendment_reconciliation_operation(
        amendment.amendment_id
    )
    try:
        stores.event_ledger.append_amendment(
            amendment,
            reconciliation_operation=reconciliation_operation,
        )
    except (KeyError, PermissionError, ValueError) as exc:
        # A mismatched/competing amendment is not an instruction to alter an
        # existing audit fact.  The target stays hidden from a foreign caller.
        if isinstance(exc, PermissionError):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Event not found"
            ) from exc
        existing = stores.event_ledger.amendment_for_target(target.event_id)
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="Event cannot be voided"
            ) from exc
        amendment = existing
    else:
        persisted = stores.event_ledger.amendment_for_target(target.event_id)
        if persisted is None:  # pragma: no cover - defensive durable-store invariant
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Retry correction"
            )
        amendment = persisted

    # The ledger is authoritative and already fail-closed for all canonical
    # reads.  Reconcile the only live canonical reducer through a durable,
    # token-fenced queue row; a failed retraction remains visible/retryable
    # instead of being hidden behind a successful correction response.
    from traittutor.learning.event_chain import PERSONALIZATION_BKT_OPERATION

    claim = stores.event_ledger.claim_derived(
        target.event_id,
        reconciliation_operation,
        now=datetime.now(UTC).isoformat(),
    )
    if claim is None and not stores.event_ledger.derived_is_applied(
        target.event_id, reconciliation_operation
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Correction is pending projection rebuild",
        )
    if claim is not None and stores.event_ledger.derived_is_applied(
        target.event_id, PERSONALIZATION_BKT_OPERATION
    ):
        try:
            from traittutor.personalization.service import get_personalization_service

            retracted = await get_personalization_service().delete_evidence(target.event_id)
        except Exception as exc:  # pragma: no cover - operational failure path
            logger.exception("canonical correction personalization retraction failed")
            stores.event_ledger.mark_derived_failed(
                target.event_id,
                reconciliation_operation,
                exc,
                now=datetime.now(UTC).isoformat(),
                claim_token=claim.token,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Correction is pending projection rebuild",
            ) from exc
        if not retracted:
            error = RuntimeError("canonical personalization signal is unavailable")
            stores.event_ledger.mark_derived_failed(
                target.event_id,
                reconciliation_operation,
                error,
                now=datetime.now(UTC).isoformat(),
                claim_token=claim.token,
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Correction is pending projection rebuild",
            )
    if claim is not None:
        stores.event_ledger.mark_derived_applied(
            target.event_id,
            reconciliation_operation,
            claim_token=claim.token,
        )

    return LearnerEventAmendmentReceipt(
        amendment_id=amendment.amendment_id,
        target_event_id=amendment.target_event_id,
        action=amendment.action,
        created_at=amendment.created_at,
    )


__all__ = [
    "GovernanceStoreBundle",
    "default_governance_store_factory",
    "get_learning_governance_service",
    "governance_store_factory",
    "router",
]
