"""Owner-bound application service for canonical memory management APIs."""

from __future__ import annotations

from traittutor.context_assembler.access import MemoryAccessRecord

from .api_models import (
    CandidateActivationRequest,
    CandidateRejectionRequest,
    CreateMemoryGrantRequest,
    MemoryDeleteResult,
    MemoryGrant,
    MemoryManagementSnapshot,
    MemoryMutationRequest,
    MemorySearchRequest,
)
from .index_projection import MemoryIndex, build_memory_index
from .index_store import MemoryIndexRebuildToken, MemoryIndexStore
from .models import MemoryCandidate, MemoryConflict, MemoryScope, UserMemoryItem
from .store import MemoryAuthorizationError, MemoryStore


class MemoryManagementService:
    """Coordinate canonical state first and derived index invalidation second.

    ``owner_id`` must be derived by the future router from the authenticated
    request.  It is deliberately absent from every request DTO.
    """

    def __init__(
        self,
        owner_id: str,
        *,
        store: MemoryStore | None = None,
        index_store: MemoryIndexStore | None = None,
    ) -> None:
        if not owner_id.strip():
            raise ValueError("owner_id is required")
        self.owner_id = owner_id
        self.store = store or MemoryStore(owner_id)
        self.index_store = index_store or MemoryIndexStore(owner_id)
        if self.store.owner_id != owner_id or self.index_store.owner_id != owner_id:
            raise ValueError("all stores must be bound to the authenticated owner")
        self.index_store.retire_legacy_indexes()

    def snapshot(self) -> MemoryManagementSnapshot:
        return MemoryManagementSnapshot(
            candidates=tuple(self.store.list_candidates()),
            items=tuple(self.store.list_items()),
            conflicts=tuple(self.store.conflicts()),
            grants=tuple(self.store.list_grants()),
            access_records=tuple(self.store.list_access_records()),
        )

    def list_candidates(self, *, status: str | None = None) -> list[MemoryCandidate]:
        return self.store.list_candidates(status=status)

    def list_items(self, *, status: str | None = None) -> list[UserMemoryItem]:
        return self.store.list_items(status=status)

    def list_conflicts(self) -> list[MemoryConflict]:
        return self.store.conflicts()

    def list_access_records(self, snapshot_id: str | None = None) -> list[MemoryAccessRecord]:
        return self.store.list_access_records(snapshot_id)

    def activate_candidate(
        self, candidate_id: str, request: CandidateActivationRequest
    ) -> UserMemoryItem:
        return self.store.activate_candidate(
            candidate_id,
            confirmed=request.confirmed,
            evidence_count=request.evidence_count,
            source="memory_management:activate",
            operation_id=request.operation_id,
        )

    def reject_candidate(
        self, candidate_id: str, request: CandidateRejectionRequest
    ) -> MemoryCandidate:
        return self.store.reject_candidate(
            candidate_id,
            source="memory_management:reject",
            operation_id=request.operation_id,
        )

    def deactivate(self, memory_id: str, request: MemoryMutationRequest) -> MemoryDeleteResult:
        item = self.store.deactivate(
            memory_id,
            source="memory_management:deactivate",
            operation_id=request.operation_id,
        )
        generation = self.index_store.invalidate_memory(
            memory_id,
            operation_id=f"deactivate:{request.operation_id}:{memory_id}",
        )
        return MemoryDeleteResult(item=item, invalidated_index_generation=generation)

    def delete(self, memory_id: str, request: MemoryMutationRequest) -> MemoryDeleteResult:
        before = {item.memory_id: item.status for item in self.store.list_items()}
        item = self.store.delete(
            memory_id,
            source="memory_management:delete",
            operation_id=request.operation_id,
        )
        after = {current.memory_id: current.status for current in self.store.list_items()}
        invalidated = [
            item_id
            for item_id, status in after.items()
            if status == "deleted" and before.get(item_id) != "deleted"
        ]
        generation = self.index_store.current_generation()
        for deleted_id in invalidated or [memory_id]:
            generation = self.index_store.invalidate_memory(
                deleted_id,
                operation_id=f"delete:{request.operation_id}:{deleted_id}",
            )
        return MemoryDeleteResult(item=item, invalidated_index_generation=generation)

    def create_grant(self, request: CreateMemoryGrantRequest) -> MemoryGrant:
        return self.store.create_grant(
            requesting_scope=request.requesting_scope,
            requesting_scope_id=request.requesting_scope_id,
            requesting_subject_id=request.requesting_subject_id,
            requesting_kc_id=request.requesting_kc_id,
            target_scope=request.target_scope,
            target_scope_id=request.target_scope_id,
            target_subject_id=request.target_subject_id,
            target_kc_id=request.target_kc_id,
            purpose=request.purpose,
            expires_at=request.expires_at,
            operation_id=request.operation_id,
        )

    def revoke_grant(self, grant_id: str) -> MemoryGrant:
        return self.store.revoke_grant(grant_id)

    def begin_index_rebuild(self) -> MemoryIndexRebuildToken:
        return self.index_store.begin_rebuild()

    def build_memory_index(
        self,
        token: MemoryIndexRebuildToken,
        *,
        entry_id: str,
        scope: MemoryScope | None = None,
    ) -> MemoryIndex:
        """Build only from this owner's active canonical records."""
        items = self.store.list_items(status="active")
        if scope is not None:
            items = [item for item in items if item.scope == scope]
        return build_memory_index(
            owner_id=self.owner_id,
            entry_id=entry_id,
            generation=token.generation,
            items=items,
        )

    def commit_index_rebuild(
        self, token: MemoryIndexRebuildToken, indexes: tuple[MemoryIndex, ...]
    ) -> tuple[MemoryIndex, ...]:
        """Resolve the source allowlist from current canonical truth at commit time."""
        active_ids = {item.memory_id for item in self.store.list_items(status="active")}
        return self.index_store.commit_rebuild(
            token,
            indexes,
            allowed_memory_ids=active_ids,
        )

    def search(self, request: MemorySearchRequest) -> list[UserMemoryItem]:
        if request.requesting_scope is not None and request.scope is None:
            raise MemoryAuthorizationError("contextual retrieval requires an explicit target scope")
        crosses_scope = request.requesting_scope is not None and (
            request.scope not in (None, request.requesting_scope)
            or (
                request.scope == request.requesting_scope
                and request.requesting_scope_id is not None
                and request.scope_id != request.requesting_scope_id
            )
            or (
                request.requesting_subject_id is not None
                and request.subject_id != request.requesting_subject_id
            )
            or (
                request.scope == "subject"
                and request.requesting_kc_id is not None
                and request.kc_id != request.requesting_kc_id
            )
        )
        authorized = False
        if crosses_scope:
            if request.grant_id is None or request.requesting_scope is None:
                raise MemoryAuthorizationError("cross-scope read requires an active grant")
            authorized = self.store.grant_authorizes(
                request.grant_id,
                requesting_scope=request.requesting_scope,
                requesting_scope_id=request.requesting_scope_id,
                requesting_subject_id=request.requesting_subject_id,
                requesting_kc_id=request.requesting_kc_id,
                target_scope=request.scope or request.requesting_scope,
                target_scope_id=request.scope_id,
                target_subject_id=request.subject_id,
                target_kc_id=request.kc_id,
                purpose=request.purpose,
            )
            if not authorized:
                raise MemoryAuthorizationError("cross-scope read requires an active grant")
        return self.store.search(
            scope=request.scope,
            scope_id=request.scope_id,
            subject_id=request.subject_id,
            kc_id=request.kc_id,
            keyword=request.keyword,
            requesting_scope=request.requesting_scope,
            requesting_scope_id=request.requesting_scope_id,
            requesting_subject_id=request.requesting_subject_id,
            requesting_kc_id=request.requesting_kc_id,
            cross_scope_authorized=authorized,
            snapshot_id=request.snapshot_id,
            purpose=request.purpose,
        )


__all__ = ["MemoryManagementService"]
