"""Owner-bound canonical memory management API."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from traittutor.context_assembler.access import MemoryAccessRecord
from traittutor.memory.api_models import (
    CandidateActivationRequest,
    CandidateRejectionRequest,
    CreateMemoryGrantRequest,
    MemoryGrant,
    MemoryMutationRequest,
    MemorySearchRequest,
)
from traittutor.memory.index_store import StaleMemoryIndexGenerationError
from traittutor.memory.management import MemoryManagementService
from traittutor.memory.models import (
    MemoryCandidate,
    MemoryConflict,
    MemoryProvenance,
    MemoryScope,
    MemorySensitivity,
    UserMemoryItem,
)
from traittutor.memory.runtime import get_current_memory_store
from traittutor.memory.store import MemoryActivationError, MemoryAuthorizationError
from traittutor.multi_user.context import get_current_user
from traittutor.multi_user.models import CurrentUser

router = APIRouter(prefix="/memories")

CandidateStatus = Literal["candidate", "conflict", "activated", "rejected", "deleted"]
ItemStatus = Literal["candidate", "active", "superseded", "dormant", "deleted"]


class MemoryCandidatePublic(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str
    scope: MemoryScope
    scope_id: str | None
    subject_id: str | None
    kc_id: str | None
    key: str
    value: str
    provenance: MemoryProvenance
    status: CandidateStatus
    confidence: float
    sensitivity: MemorySensitivity
    evidence_refs: tuple[str, ...]
    source_ref: str | None
    proposed_supersedes_id: str | None
    conflict_memory_ids: tuple[str, ...]
    valid_from: str | None
    valid_until: str | None
    created_at: str


class MemoryItemPublic(BaseModel):
    """Public item view; deletion removes content and provenance payloads."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    memory_id: str
    scope: MemoryScope
    scope_id: str | None
    subject_id: str | None
    kc_id: str | None
    key: str
    value: str | None
    redacted: bool
    provenance: MemoryProvenance
    status: ItemStatus
    confidence: float
    sensitivity: MemorySensitivity
    valid_from: str
    valid_until: str | None
    supersedes_id: str | None
    evidence_refs: tuple[str, ...]
    source_ref: str | None
    created_at: str
    updated_at: str


class MemoryConflictPublic(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: MemoryScope
    scope_id: str | None
    subject_id: str | None
    kc_id: str | None
    key: str
    candidate_id: str
    candidate_value: str
    memory_ids: tuple[str, ...]
    values: tuple[str, ...]


class MemoryGrantPublic(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    grant_id: str
    requesting_scope: MemoryScope
    requesting_scope_id: str | None
    requesting_subject_id: str | None
    requesting_kc_id: str | None
    target_scope: MemoryScope
    target_scope_id: str | None
    target_subject_id: str | None
    target_kc_id: str | None
    purpose: str
    status: Literal["active", "revoked"]
    created_at: str
    revoked_at: str | None


class MemoryMutationPublic(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    item: MemoryItemPublic
    invalidated_index_generation: int = Field(ge=0)


class LongTermIndexEntryPublic(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    entry_id: str
    index_version: int = Field(ge=1)
    generation: int = Field(ge=0)
    content_hash: str
    claim_count: int = Field(ge=0)
    assertion_states: tuple[str, ...]
    updated_at: str


class LongTermIndexStatusPublic(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    generation: int = Field(ge=0)
    entries: tuple[LongTermIndexEntryPublic, ...]


class LongTermIndexRebuildRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    entry_id: str = Field(min_length=1, max_length=128)
    scope: MemoryScope | None = None


MemoryServiceFactory = Callable[[CurrentUser], MemoryManagementService]


def default_memory_service_factory(user: CurrentUser) -> MemoryManagementService:
    """Construct stores only after authentication has installed request context."""
    return MemoryManagementService(user.id, store=get_current_memory_store(user.id))


memory_service_factory: MemoryServiceFactory = default_memory_service_factory


def get_memory_management_service() -> MemoryManagementService:
    user = get_current_user()
    return memory_service_factory(user)


MemoryService = Annotated[
    MemoryManagementService,
    Depends(get_memory_management_service),
]


def _candidate_public(candidate: MemoryCandidate) -> MemoryCandidatePublic:
    return MemoryCandidatePublic.model_validate(
        candidate.model_dump(mode="json", exclude={"owner_id"})
    )


def _item_public(item: UserMemoryItem) -> MemoryItemPublic:
    deleted = item.status == "deleted"
    return MemoryItemPublic(
        memory_id=item.memory_id,
        scope=item.scope,
        scope_id=item.scope_id,
        subject_id=item.subject_id,
        kc_id=item.kc_id,
        key=item.key,
        value=None if deleted else item.value,
        redacted=deleted,
        provenance=item.provenance,
        status=item.status,
        confidence=item.confidence,
        sensitivity=item.sensitivity,
        valid_from=item.valid_from,
        valid_until=item.valid_until,
        supersedes_id=item.supersedes_id,
        evidence_refs=() if deleted else item.evidence_refs,
        source_ref=None if deleted else item.source_ref,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def _conflict_public(conflict: MemoryConflict) -> MemoryConflictPublic:
    return MemoryConflictPublic.model_validate(conflict.model_dump(mode="json"))


def _grant_public(grant: MemoryGrant) -> MemoryGrantPublic:
    return MemoryGrantPublic.model_validate(grant.model_dump(mode="json", exclude={"owner_id"}))


def _long_term_index_status(service: MemoryManagementService) -> LongTermIndexStatusPublic:
    entries = tuple(
        LongTermIndexEntryPublic(
            entry_id=index.entry_id,
            index_version=index.index_version,
            generation=index.generation,
            content_hash=index.content_hash,
            claim_count=len(index.claims),
            assertion_states=tuple(sorted({claim.assertion_state for claim in index.claims})),
            updated_at=index.updated_at,
        )
        for index in service.index_store.list_indexes()
    )
    return LongTermIndexStatusPublic(
        generation=service.index_store.current_generation(),
        entries=entries,
    )


def _not_found() -> HTTPException:
    # One response for missing and cross-owner IDs prevents object enumeration.
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memory object not found")


async def _reconcile_derived_preferences() -> None:
    """Propagate canonical mutations into derived learner-support state inline.

    AGENTS.md invariant: deleted, deactivated or superseded information must
    not enter later snapshots or index generations. Awaiting reconciliation
    inside the mutation response keeps derived signals, learner profiles and
    frozen per-session memory snapshots consistent with canonical truth
    before the caller can observe the mutation result. ``reconcile_memory``
    converts its own failures into a status record and never raises, so a
    reconcile hiccup cannot fail an already-committed canonical mutation.
    """
    from traittutor.personalization.service import get_personalization_service

    await get_personalization_service().reconcile_memory()


@router.get("/candidates", response_model=list[MemoryCandidatePublic])
def list_candidates(
    service: MemoryService,
    candidate_status: Annotated[CandidateStatus | None, Query(alias="status")] = None,
) -> list[MemoryCandidatePublic]:
    return [_candidate_public(item) for item in service.list_candidates(status=candidate_status)]


@router.get("/candidates/{candidate_id}", response_model=MemoryCandidatePublic)
def get_candidate(candidate_id: str, service: MemoryService) -> MemoryCandidatePublic:
    try:
        return _candidate_public(service.store.candidate(candidate_id))
    except KeyError as exc:
        raise _not_found() from exc


@router.post("/candidates/{candidate_id}/activate", response_model=MemoryItemPublic)
async def activate_candidate(
    candidate_id: str,
    request: CandidateActivationRequest,
    service: MemoryService,
) -> MemoryItemPublic:
    try:
        item = service.activate_candidate(candidate_id, request)
    except KeyError as exc:
        raise _not_found() from exc
    except MemoryActivationError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await _reconcile_derived_preferences()
    return _item_public(item)


@router.post("/candidates/{candidate_id}/reject", response_model=MemoryCandidatePublic)
def reject_candidate(
    candidate_id: str,
    request: CandidateRejectionRequest,
    service: MemoryService,
) -> MemoryCandidatePublic:
    try:
        return _candidate_public(service.reject_candidate(candidate_id, request))
    except KeyError as exc:
        raise _not_found() from exc
    except MemoryActivationError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("", response_model=list[MemoryItemPublic])
@router.get("/items", response_model=list[MemoryItemPublic])
def list_items(
    service: MemoryService,
    item_status: Annotated[ItemStatus | None, Query(alias="status")] = None,
) -> list[MemoryItemPublic]:
    return [_item_public(item) for item in service.list_items(status=item_status)]


@router.get("/items/{memory_id}", response_model=MemoryItemPublic)
def get_item(memory_id: str, service: MemoryService) -> MemoryItemPublic:
    try:
        return _item_public(service.store.item(memory_id))
    except KeyError as exc:
        raise _not_found() from exc


@router.post("/items/{memory_id}/deactivate", response_model=MemoryMutationPublic)
async def deactivate_item(
    memory_id: str,
    request: MemoryMutationRequest,
    service: MemoryService,
) -> MemoryMutationPublic:
    try:
        result = service.deactivate(memory_id, request)
    except KeyError as exc:
        raise _not_found() from exc
    except MemoryActivationError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await _reconcile_derived_preferences()
    return MemoryMutationPublic(
        item=_item_public(result.item),
        invalidated_index_generation=result.invalidated_index_generation,
    )


@router.delete("/items/{memory_id}", response_model=MemoryMutationPublic)
async def delete_item(
    memory_id: str,
    request: MemoryMutationRequest,
    service: MemoryService,
) -> MemoryMutationPublic:
    try:
        result = service.delete(memory_id, request)
    except KeyError as exc:
        raise _not_found() from exc
    await _reconcile_derived_preferences()
    return MemoryMutationPublic(
        item=_item_public(result.item),
        invalidated_index_generation=result.invalidated_index_generation,
    )


@router.get("/conflicts", response_model=list[MemoryConflictPublic])
def list_conflicts(service: MemoryService) -> list[MemoryConflictPublic]:
    return [_conflict_public(item) for item in service.list_conflicts()]


@router.post("/conflicts/{candidate_id}/supersede", response_model=MemoryItemPublic)
async def supersede_conflict(
    candidate_id: str,
    request: CandidateActivationRequest,
    service: MemoryService,
) -> MemoryItemPublic:
    conflict = next(
        (item for item in service.list_conflicts() if item.candidate_id == candidate_id),
        None,
    )
    if conflict is None:
        raise _not_found()
    if not request.confirmed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="supersede requires explicit confirmation",
        )
    try:
        item = service.activate_candidate(candidate_id, request)
    except (KeyError, MemoryActivationError) as exc:
        if isinstance(exc, KeyError):
            raise _not_found() from exc
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await _reconcile_derived_preferences()
    return _item_public(item)


@router.get("/grants", response_model=list[MemoryGrantPublic])
def list_grants(service: MemoryService) -> list[MemoryGrantPublic]:
    return [_grant_public(grant) for grant in service.store.list_grants()]


@router.post(
    "/grants",
    response_model=MemoryGrantPublic,
    status_code=status.HTTP_201_CREATED,
)
def create_grant(
    request: CreateMemoryGrantRequest,
    service: MemoryService,
) -> MemoryGrantPublic:
    return _grant_public(service.create_grant(request))


@router.delete("/grants/{grant_id}", response_model=MemoryGrantPublic)
def revoke_grant(grant_id: str, service: MemoryService) -> MemoryGrantPublic:
    try:
        return _grant_public(service.revoke_grant(grant_id))
    except KeyError as exc:
        raise _not_found() from exc


@router.post("/search", response_model=list[MemoryItemPublic])
def search_memory(
    request: MemorySearchRequest,
    service: MemoryService,
) -> list[MemoryItemPublic]:
    try:
        return [_item_public(item) for item in service.search(request)]
    except MemoryAuthorizationError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Memory access denied"
        ) from exc


@router.get("/access-records", response_model=list[MemoryAccessRecord])
def list_access_records(
    service: MemoryService,
    snapshot_id: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
) -> list[MemoryAccessRecord]:
    return service.list_access_records(snapshot_id)


@router.get("/index/status", response_model=LongTermIndexStatusPublic)
def get_long_term_index_status(service: MemoryService) -> LongTermIndexStatusPublic:
    return _long_term_index_status(service)


@router.post("/index/rebuild", response_model=LongTermIndexStatusPublic)
def rebuild_long_term_index(
    request: LongTermIndexRebuildRequest,
    service: MemoryService,
) -> LongTermIndexStatusPublic:
    token = service.begin_index_rebuild()
    index = service.build_memory_index(
        token,
        entry_id=request.entry_id,
        scope=request.scope,
    )
    try:
        service.commit_index_rebuild(token, (index,))
    except StaleMemoryIndexGenerationError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Long-term memory index rebuild is stale"
        ) from exc
    return _long_term_index_status(service)


__all__ = [
    "default_memory_service_factory",
    "get_memory_management_service",
    "memory_service_factory",
    "router",
]
