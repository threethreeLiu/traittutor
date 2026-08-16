"""Owner-bound, file-locked persistence for scoped canonical memory.

The repository is the single canonical write path for v2.7 memory. Reads are
always owner-bound; cross-scope reads additionally require an explicit grant
and produce a durable ``MemoryAccessRecord`` for the Why Drawer.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
import hashlib
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterable, Iterator, Literal, Sequence
from uuid import uuid4

from traittutor.context_assembler.access import MemoryAccessRecord
from traittutor.services.path_service import PathService
from traittutor.unified_storage import SectionedRecordStore

from .api_models import MemoryGrant
from .models import (
    MemoryCandidate,
    MemoryConflict,
    MemoryLifecycleRecord,
    MemoryScope,
    MemorySensitivity,
    UserMemoryItem,
)
from .retrieval import (
    MAX_MEMORY_RESULTS,
    MAX_MEMORY_TOKEN_BUDGET,
    MemoryHybridSearchResult,
    rank_memory_candidates,
)

if TYPE_CHECKING:
    from .index_store import MemoryIndexStore

EmbeddingBatch = Callable[[list[str]], Sequence[Sequence[float]]]

ACTIVATION_EVIDENCE_THRESHOLD = 2
_SCHEMA_VERSION = 2

# Audit trail bound: the newest N cross-scope access records are retained.
# Every search with a snapshot_id appends records; unbounded growth made each
# subsequent write heavier (and eventually multi-megabyte payloads per turn).
# The append path trims oldest-first by insertion order.
MAX_ACCESS_RECORDS = 20_000


class MemoryActivationError(ValueError):
    """An activation would violate the provenance/source gate."""


class MemoryStoreError(RuntimeError):
    """The durable memory store cannot safely serve a request."""


class MemoryAuthorizationError(PermissionError):
    """A cross-scope or cross-partition read lacks explicit authorization."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _is_clickable_source(source_ref: str | None) -> bool:
    return bool(source_ref and source_ref.startswith(("https://", "http://")))


def _validate_partition(
    scope: MemoryScope,
    scope_id: str | None,
    subject_id: str | None,
    kc_id: str | None,
) -> None:
    """Enforce PRD scope isolation for every newly written memory."""
    if scope == "global":
        if scope_id is not None or subject_id is not None or kc_id is not None:
            raise ValueError("global memory cannot carry scope, subject, or KC identifiers")
        return
    if scope == "subject":
        if scope_id is not None:
            raise ValueError("subject memory uses subject_id, not scope_id")
        if subject_id is None:
            raise ValueError("subject memory requires subject_id")
        return
    if scope_id is None:
        raise ValueError(f"{scope} memory requires scope_id")
    if kc_id is not None and subject_id is None:
        raise ValueError("KC-scoped memory requires subject_id")


class MemoryStore:
    """Durable lifecycle repository bound to one authenticated owner.

    ``owner_id`` defaults to ``local`` only for the historical single-user
    runtime. Multi-user callers must construct one repository per identity.
    The owner is never accepted as a search filter, so callers cannot broaden
    a read accidentally.
    """

    def __init__(
        self,
        owner_id: str = "local",
        *,
        path: Path | None = None,
        index_store: MemoryIndexStore | None = None,
        embedding_batch: EmbeddingBatch | None = None,
        path_service: PathService | None = None,
        db_path: Any | None = None,
    ) -> None:
        if not owner_id.strip():
            raise ValueError("owner_id is required")
        self.owner_id = owner_id
        # ``path`` is retained only to keep legacy ``path=`` constructors
        # working; the adapter derives an isolated DB location from it and never
        # reads or writes it as a file.
        self._store_path = path
        self._adapter = SectionedRecordStore(
            "memory_v2",
            owner_id,
            schema_version=_SCHEMA_VERSION,
            path_service=path_service,
            db_path=db_path,
            legacy_path=path,
        )
        if index_store is None and path is None:
            from .index_store import MemoryIndexStore

            index_store = MemoryIndexStore(owner_id)
        if index_store is not None and index_store.owner_id != owner_id:
            raise ValueError("memory index store must use the same owner")
        self._index_store = index_store
        self._embedding_batch = embedding_batch

    def _embed_texts(self, texts: list[str]) -> Sequence[Sequence[float]]:
        if self._embedding_batch is not None:
            return self._embedding_batch(texts)
        if self._store_path is not None:
            raise RuntimeError("no embedding provider is configured for this memory store")
        from traittutor.services.embedding import get_embedding_client

        return get_embedding_client().embed_sync(texts)

    def _load(self) -> dict[str, Any]:
        """Read the full memory payload (all six sections) from the unified DB."""
        return self._adapter.snapshot()

    def _receipt(
        self, payload: dict[str, Any], operation_id: str | None, action: str
    ) -> dict[str, Any] | None:
        if operation_id is None:
            return None
        return next(
            (
                receipt
                for receipt in payload["mutation_receipts"]
                if receipt.get("owner_id") == self.owner_id
                and receipt.get("operation_id") == operation_id
                and receipt.get("action") == action
            ),
            None,
        )

    def _save_receipt(
        self,
        payload: dict[str, Any],
        *,
        operation_id: str | None,
        action: str,
        resource_id: str,
    ) -> None:
        if operation_id is None:
            return
        payload["mutation_receipts"].append(
            {
                "owner_id": self.owner_id,
                "operation_id": operation_id,
                "action": action,
                "resource_id": resource_id,
                "created_at": _now(),
            }
        )

    def _save(self, payload: dict[str, Any]) -> None:
        """Replace the full memory payload inside the active transaction."""
        self._adapter.replace_all(payload)

    @contextmanager
    def _locked(self) -> Iterator[dict[str, Any]]:
        """Exclusive load + mutate block; one immediate DB transaction."""
        with self._adapter.locked() as payload:
            yield payload

    def _record(
        self,
        payload: dict[str, Any],
        *,
        action: Literal[
            "candidate", "conflict", "activate", "supersede", "deactivate", "delete", "reject"
        ],
        resulting_status: Literal[
            "candidate",
            "conflict",
            "activated",
            "rejected",
            "active",
            "superseded",
            "dormant",
            "deleted",
        ],
        source: str,
        memory_id: str | None = None,
        candidate_id: str | None = None,
        source_ref: str | None = None,
        created_at: str | None = None,
    ) -> None:
        record = MemoryLifecycleRecord(
            record_id=f"mlr_{uuid4().hex[:16]}",
            owner_id=self.owner_id,
            memory_id=memory_id,
            candidate_id=candidate_id,
            action=action,
            resulting_status=resulting_status,
            source=source,
            source_ref=source_ref,
            created_at=created_at or _now(),
        )
        payload["lifecycle"].append(record.model_dump(mode="json"))

    def propose_candidate(
        self,
        *,
        scope: MemoryScope,
        key: str,
        value: str,
        provenance: Literal["explicit", "inferred"],
        confidence: float,
        scope_id: str | None = None,
        subject_id: str | None = None,
        kc_id: str | None = None,
        sensitivity: MemorySensitivity = "personal",
        evidence_refs: Iterable[str] = (),
        source_ref: str | None = None,
        source: str = "user_or_model_proposal",
        valid_from: str | None = None,
        valid_until: str | None = None,
        created_at: str | None = None,
        candidate_id: str | None = None,
    ) -> MemoryCandidate:
        _validate_partition(scope, scope_id, subject_id, kc_id)
        with self._locked() as payload:
            if candidate_id is not None:
                existing_candidate = next(
                    (
                        entry
                        for entry in payload["candidates"]
                        if entry.get("candidate_id") == candidate_id
                        and entry.get("owner_id") == self.owner_id
                    ),
                    None,
                )
                if existing_candidate is not None:
                    return MemoryCandidate.model_validate(existing_candidate)
            existing_record = self._active_record(payload, scope, scope_id, subject_id, kc_id, key)
            existing = (
                UserMemoryItem.model_validate(existing_record)
                if existing_record is not None
                else None
            )
            conflict_ids = (
                (existing.memory_id,)
                if existing is not None and existing.value.casefold() != value.casefold()
                else ()
            )
            candidate = MemoryCandidate(
                candidate_id=candidate_id or f"cand_{uuid4().hex[:16]}",
                owner_id=self.owner_id,
                scope=scope,
                scope_id=scope_id,
                subject_id=subject_id,
                kc_id=kc_id,
                key=key,
                value=value,
                provenance=provenance,
                status="conflict" if conflict_ids else "candidate",
                confidence=confidence,
                sensitivity=sensitivity,
                evidence_refs=tuple(evidence_refs),
                source_ref=source_ref,
                proposed_supersedes_id=existing.memory_id if existing is not None else None,
                conflict_memory_ids=conflict_ids,
                valid_from=valid_from,
                valid_until=valid_until,
                created_at=created_at or _now(),
            )
            payload["candidates"].append(candidate.model_dump(mode="json"))
            self._record(
                payload,
                action="candidate",
                resulting_status="candidate",
                source=source,
                candidate_id=candidate.candidate_id,
                source_ref=source_ref,
                created_at=candidate.created_at,
            )
            if conflict_ids:
                self._record(
                    payload,
                    action="conflict",
                    resulting_status="conflict",
                    source=source,
                    candidate_id=candidate.candidate_id,
                    source_ref=source_ref,
                    created_at=candidate.created_at,
                )
            self._save(payload)
        return candidate

    def save_explicit_preference(
        self,
        *,
        value: str,
        operation_id: str,
        target_memory_id: str | None = None,
    ) -> tuple[UserMemoryItem, bool]:
        """Save one user-stated preference without the legacy Markdown path.

        Returns ``(item, deduplicated)``. Edits preserve the original semantic
        key so the canonical lifecycle records a supersession instead of
        creating an unrelated preference.
        """
        normalized = value.strip()
        if not normalized:
            raise ValueError("preference value is required")
        with self._locked() as payload:
            receipt = self._receipt(payload, operation_id, "save_explicit_preference")
            if receipt is not None:
                return self._owned_item(payload, str(receipt["resource_id"])), False

            active_preferences = [
                UserMemoryItem.model_validate(row)
                for row in payload["items"]
                if row.get("owner_id") == self.owner_id
                and row.get("status") == "active"
                and str(row.get("key", "")).startswith("preference:")
            ]
            duplicate = next(
                (
                    item
                    for item in active_preferences
                    if item.value.strip().casefold() == normalized.casefold()
                ),
                None,
            )
            if duplicate is not None:
                self._save_receipt(
                    payload,
                    operation_id=operation_id,
                    action="save_explicit_preference",
                    resource_id=duplicate.memory_id,
                )
                self._save(payload)
                return duplicate, True

            if target_memory_id is not None:
                target = self._owned_item(payload, target_memory_id)
                if target.status != "active" or not target.key.startswith("preference:"):
                    raise MemoryActivationError("target is not an active preference")
                key = target.key
            else:
                digest = hashlib.sha256(normalized.casefold().encode("utf-8")).hexdigest()[:16]
                key = f"preference:{digest}"

            item = self._commit_unlocked(
                payload,
                scope="global",
                scope_id=None,
                subject_id=None,
                kc_id=None,
                key=key,
                value=normalized,
                provenance="explicit",
                confidence=1.0,
                sensitivity="personal",
                evidence_refs=(operation_id,),
                source_ref=None,
                source="write_memory:explicit_preference",
            )
            self._save_receipt(
                payload,
                operation_id=operation_id,
                action="save_explicit_preference",
                resource_id=item.memory_id,
            )
            self._save(payload)
            return item, False

    def candidate(self, candidate_id: str) -> MemoryCandidate:
        record = next(
            (
                item
                for item in self._load()["candidates"]
                if item.get("candidate_id") == candidate_id
                and item.get("owner_id") == self.owner_id
            ),
            None,
        )
        if record is None:
            raise KeyError(candidate_id)
        return MemoryCandidate.model_validate(record)

    def add_explicit(
        self,
        *,
        scope: MemoryScope,
        key: str,
        value: str,
        scope_id: str | None = None,
        subject_id: str | None = None,
        kc_id: str | None = None,
        confidence: float = 1.0,
        sensitivity: MemorySensitivity = "personal",
        evidence_refs: Iterable[str] = (),
        source_ref: str | None = None,
        source: str = "explicit_user_statement",
    ) -> UserMemoryItem:
        _validate_partition(scope, scope_id, subject_id, kc_id)
        if scope == "research" and not _is_clickable_source(source_ref):
            raise MemoryActivationError("research-sourced memory requires a clickable source_ref")
        return self._commit(
            scope=scope,
            scope_id=scope_id,
            subject_id=subject_id,
            kc_id=kc_id,
            key=key,
            value=value,
            provenance="explicit",
            confidence=confidence,
            sensitivity=sensitivity,
            evidence_refs=tuple(evidence_refs),
            source_ref=source_ref,
            source=source,
        )

    def activate_candidate(
        self,
        candidate_id: str,
        *,
        confirmed: bool = False,
        evidence_count: int = 0,
        source: str = "candidate_activation",
        operation_id: str | None = None,
    ) -> UserMemoryItem:
        with self._locked() as payload:
            receipt = self._receipt(payload, operation_id, "activate_candidate")
            if receipt is not None:
                return self._owned_item(payload, str(receipt["resource_id"]))
            record = next(
                (
                    entry
                    for entry in payload["candidates"]
                    if entry.get("candidate_id") == candidate_id
                    and entry.get("owner_id") == self.owner_id
                ),
                None,
            )
            if record is None:
                raise KeyError(candidate_id)
            candidate = MemoryCandidate.model_validate(record)
            if candidate.status == "activated":
                lifecycle = next(
                    (
                        row
                        for row in reversed(payload["lifecycle"])
                        if row.get("owner_id") == self.owner_id
                        and row.get("candidate_id") == candidate_id
                        and row.get("action") == "activate"
                    ),
                    None,
                )
                if lifecycle is None or lifecycle.get("memory_id") is None:
                    raise MemoryStoreError("activated candidate is missing its lifecycle link")
                return self._owned_item(payload, str(lifecycle["memory_id"]))
            if candidate.status not in ("candidate", "conflict"):
                raise MemoryActivationError(f"candidate cannot activate from {candidate.status}")
            if (
                candidate.provenance == "inferred"
                and not confirmed
                and evidence_count < ACTIVATION_EVIDENCE_THRESHOLD
            ):
                raise MemoryActivationError(
                    "inferred memory requires user confirmation or repeated evidence before activation"
                )
            if candidate.scope == "research" and not _is_clickable_source(candidate.source_ref):
                raise MemoryActivationError(
                    "research-sourced memory requires a clickable source_ref"
                )
            item = self._commit_unlocked(
                payload,
                scope=candidate.scope,
                scope_id=candidate.scope_id,
                subject_id=candidate.subject_id,
                kc_id=candidate.kc_id,
                key=candidate.key,
                value=candidate.value,
                provenance=candidate.provenance,
                confidence=candidate.confidence,
                sensitivity=candidate.sensitivity,
                evidence_refs=candidate.evidence_refs,
                source_ref=candidate.source_ref,
                source=source,
                candidate_id=candidate_id,
            )
            record["status"] = "activated"
            self._save_receipt(
                payload,
                operation_id=operation_id,
                action="activate_candidate",
                resource_id=item.memory_id,
            )
            self._save(payload)
        return item

    def reject_candidate(
        self,
        candidate_id: str,
        *,
        source: str = "user_rejection",
        operation_id: str | None = None,
    ) -> MemoryCandidate:
        """Reject a candidate explicitly and preserve the source/status trace."""
        with self._locked() as payload:
            receipt = self._receipt(payload, operation_id, "reject_candidate")
            if receipt is not None:
                return MemoryCandidate.model_validate(
                    next(
                        row
                        for row in payload["candidates"]
                        if row.get("owner_id") == self.owner_id
                        and row.get("candidate_id") == receipt["resource_id"]
                    )
                )
            record = next(
                (
                    entry
                    for entry in payload["candidates"]
                    if entry.get("candidate_id") == candidate_id
                    and entry.get("owner_id") == self.owner_id
                ),
                None,
            )
            if record is None:
                raise KeyError(candidate_id)
            candidate = MemoryCandidate.model_validate(record)
            if candidate.status == "activated":
                raise MemoryActivationError(
                    "an activated candidate must be deactivated by memory id"
                )
            if candidate.status == "rejected":
                return candidate
            updated = candidate.model_copy(update={"status": "rejected"})
            record.clear()
            record.update(updated.model_dump(mode="json"))
            self._record(
                payload,
                action="reject",
                resulting_status="rejected",
                source=source,
                candidate_id=candidate_id,
                source_ref=candidate.source_ref,
            )
            self._save_receipt(
                payload,
                operation_id=operation_id,
                action="reject_candidate",
                resource_id=candidate_id,
            )
            self._save(payload)
        return updated

    def _commit(
        self,
        *,
        scope: MemoryScope,
        scope_id: str | None,
        subject_id: str | None,
        kc_id: str | None,
        key: str,
        value: str,
        provenance: Literal["explicit", "inferred"],
        confidence: float,
        sensitivity: MemorySensitivity,
        evidence_refs: tuple[str, ...],
        source_ref: str | None,
        source: str,
        candidate_id: str | None = None,
    ) -> UserMemoryItem:
        with self._locked() as payload:
            item = self._commit_unlocked(
                payload,
                scope=scope,
                scope_id=scope_id,
                subject_id=subject_id,
                kc_id=kc_id,
                key=key,
                value=value,
                provenance=provenance,
                confidence=confidence,
                sensitivity=sensitivity,
                evidence_refs=evidence_refs,
                source_ref=source_ref,
                source=source,
                candidate_id=candidate_id,
            )
            self._save(payload)
        return item

    def _commit_unlocked(
        self,
        payload: dict[str, Any],
        *,
        scope: MemoryScope,
        scope_id: str | None,
        subject_id: str | None,
        kc_id: str | None,
        key: str,
        value: str,
        provenance: Literal["explicit", "inferred"],
        confidence: float,
        sensitivity: MemorySensitivity,
        evidence_refs: tuple[str, ...],
        source_ref: str | None,
        source: str,
        candidate_id: str | None = None,
    ) -> UserMemoryItem:
        _validate_partition(scope, scope_id, subject_id, kc_id)
        existing_record = self._active_record(payload, scope, scope_id, subject_id, kc_id, key)
        existing = (
            UserMemoryItem.model_validate(existing_record) if existing_record is not None else None
        )
        now = _now()
        item = UserMemoryItem(
            memory_id=f"mem_{uuid4().hex[:16]}",
            owner_id=self.owner_id,
            scope=scope,
            scope_id=scope_id,
            subject_id=subject_id,
            kc_id=kc_id,
            key=key,
            value=value,
            provenance=provenance,
            status="active",
            confidence=confidence,
            sensitivity=sensitivity,
            valid_from=now,
            supersedes_id=existing.memory_id if existing else None,
            evidence_refs=evidence_refs,
            source_ref=source_ref,
            created_at=now,
            updated_at=now,
        )
        if existing is not None and existing_record is not None:
            superseded = existing.model_copy(
                update={"status": "superseded", "valid_until": now, "updated_at": now}
            )
            existing_record.clear()
            existing_record.update(superseded.model_dump(mode="json"))
            self._record(
                payload,
                action="supersede",
                resulting_status="superseded",
                source=source,
                memory_id=existing.memory_id,
                source_ref=source_ref,
                created_at=now,
            )
        payload["items"].append(item.model_dump(mode="json"))
        self._record(
            payload,
            action="activate",
            resulting_status="active",
            source=source,
            memory_id=item.memory_id,
            candidate_id=candidate_id,
            source_ref=source_ref,
            created_at=now,
        )
        return item

    def _owned_item(self, payload: dict[str, Any], memory_id: str) -> UserMemoryItem:
        record = next(
            (
                item
                for item in payload["items"]
                if item.get("owner_id") == self.owner_id and item.get("memory_id") == memory_id
            ),
            None,
        )
        if record is None:
            raise KeyError(memory_id)
        return UserMemoryItem.model_validate(record)

    def _active_record(
        self,
        payload: dict[str, Any],
        scope: MemoryScope,
        scope_id: str | None,
        subject_id: str | None,
        kc_id: str | None,
        key: str,
    ) -> dict[str, Any] | None:
        matching = [
            item
            for item in payload["items"]
            if item.get("owner_id") == self.owner_id
            and item.get("scope") == scope
            and item.get("scope_id") == scope_id
            and item.get("subject_id") == subject_id
            and item.get("kc_id") == kc_id
            and item.get("key") == key
            and item.get("status") == "active"
        ]
        return max(matching, key=lambda item: str(item.get("valid_from", "")), default=None)

    def deactivate(
        self,
        memory_id: str,
        *,
        source: str = "user_deactivation",
        operation_id: str | None = None,
    ) -> UserMemoryItem:
        return self._set_status(
            memory_id,
            "dormant",
            source=source,
            operation_id=operation_id,
        )

    def delete(
        self,
        memory_id: str,
        *,
        source: str = "user_deletion",
        cascade: bool = True,
        operation_id: str | None = None,
    ) -> UserMemoryItem:
        with self._locked() as payload:
            receipt = self._receipt(payload, operation_id, "delete")
            if receipt is not None:
                return self._owned_item(payload, str(receipt["resource_id"]))
            owned = {
                item["memory_id"]: item
                for item in payload["items"]
                if item.get("owner_id") == self.owner_id
            }
            if memory_id not in owned:
                raise KeyError(memory_id)
            existing_root = UserMemoryItem.model_validate(owned[memory_id])
            if existing_root.status == "deleted":
                self._save_receipt(
                    payload,
                    operation_id=operation_id,
                    action="delete",
                    resource_id=memory_id,
                )
                if operation_id is not None:
                    self._save(payload)
                return existing_root
            targets = {memory_id}
            if cascade:
                changed = True
                while changed:
                    before = len(targets)
                    targets.update(
                        item_id
                        for item_id, item in owned.items()
                        if item.get("supersedes_id") in targets
                    )
                    changed = len(targets) != before
            now = _now()
            root: UserMemoryItem | None = None
            for target in targets:
                item = UserMemoryItem.model_validate(owned[target])
                updated = item.model_copy(
                    update={"status": "deleted", "valid_until": now, "updated_at": now}
                )
                owned[target].clear()
                owned[target].update(updated.model_dump(mode="json"))
                self._record(
                    payload,
                    action="delete",
                    resulting_status="deleted",
                    source=source,
                    memory_id=target,
                    source_ref=item.source_ref,
                    created_at=now,
                )
                if target == memory_id:
                    root = updated
            self._save_receipt(
                payload,
                operation_id=operation_id,
                action="delete",
                resource_id=memory_id,
            )
            self._save(payload)
        assert root is not None
        return root

    def _set_status(
        self,
        memory_id: str,
        status: Literal["dormant"],
        *,
        source: str,
        operation_id: str | None = None,
    ) -> UserMemoryItem:
        with self._locked() as payload:
            receipt = self._receipt(payload, operation_id, "deactivate")
            if receipt is not None:
                return self._owned_item(payload, str(receipt["resource_id"]))
            record = next(
                (
                    item
                    for item in payload["items"]
                    if item.get("memory_id") == memory_id and item.get("owner_id") == self.owner_id
                ),
                None,
            )
            if record is None:
                raise KeyError(memory_id)
            item = UserMemoryItem.model_validate(record)
            if item.status == "dormant":
                return item
            if item.status == "deleted":
                raise MemoryActivationError("deleted memory cannot be deactivated")
            now = _now()
            updated = item.model_copy(
                update={"status": status, "valid_until": now, "updated_at": now}
            )
            record.clear()
            record.update(updated.model_dump(mode="json"))
            self._record(
                payload,
                action="deactivate",
                resulting_status=status,
                source=source,
                memory_id=memory_id,
                source_ref=item.source_ref,
                created_at=now,
            )
            self._save_receipt(
                payload,
                operation_id=operation_id,
                action="deactivate",
                resource_id=memory_id,
            )
            self._save(payload)
        return updated

    def get_active(
        self,
        scope: MemoryScope,
        key: str,
        *,
        scope_id: str | None = None,
        subject_id: str | None = None,
        kc_id: str | None = None,
    ) -> UserMemoryItem | None:
        record = self._active_record(self._load(), scope, scope_id, subject_id, kc_id, key)
        return UserMemoryItem.model_validate(record) if record is not None else None

    def item(self, memory_id: str) -> UserMemoryItem:
        """Return one owner-visible item, including inactive history."""
        return self._owned_item(self._load(), memory_id)

    def list_items(
        self,
        *,
        status: str | None = None,
        scope: MemoryScope | None = None,
        scope_id: str | None = None,
        subject_id: str | None = None,
        kc_id: str | None = None,
    ) -> list[UserMemoryItem]:
        items = [
            UserMemoryItem.model_validate(record)
            for record in self._load()["items"]
            if record.get("owner_id") == self.owner_id
            and (status is None or record.get("status") == status)
            and (scope is None or record.get("scope") == scope)
            and (scope_id is None or record.get("scope_id") == scope_id)
            and (subject_id is None or record.get("subject_id") == subject_id)
            and (kc_id is None or record.get("kc_id") == kc_id)
        ]
        return sorted(items, key=lambda item: (item.updated_at, item.memory_id), reverse=True)

    def list_candidates(self, *, status: str | None = None) -> list[MemoryCandidate]:
        candidates = [
            MemoryCandidate.model_validate(record)
            for record in self._load()["candidates"]
            if record.get("owner_id") == self.owner_id
            and (status is None or record.get("status") == status)
        ]
        return sorted(
            candidates,
            key=lambda candidate: (candidate.created_at, candidate.candidate_id),
            reverse=True,
        )

    def create_grant(
        self,
        *,
        requesting_scope: MemoryScope,
        target_scope: MemoryScope,
        purpose: str,
        requesting_scope_id: str | None = None,
        requesting_subject_id: str | None = None,
        requesting_kc_id: str | None = None,
        target_scope_id: str | None = None,
        target_subject_id: str | None = None,
        target_kc_id: str | None = None,
        expires_at: str | None = None,
        operation_id: str | None = None,
    ) -> MemoryGrant:
        """Create an explicit server-side retrieval authorization."""
        _validate_partition(
            requesting_scope,
            requesting_scope_id,
            requesting_subject_id,
            requesting_kc_id,
        )
        _validate_partition(target_scope, target_scope_id, target_subject_id, target_kc_id)
        with self._locked() as payload:
            receipt = self._receipt(payload, operation_id, "create_grant")
            if receipt is not None:
                return self._owned_grant(payload, str(receipt["resource_id"]))
            grant = MemoryGrant(
                grant_id=f"grant_{uuid4().hex[:16]}",
                owner_id=self.owner_id,
                requesting_scope=requesting_scope,
                requesting_scope_id=requesting_scope_id,
                requesting_subject_id=requesting_subject_id,
                requesting_kc_id=requesting_kc_id,
                target_scope=target_scope,
                target_scope_id=target_scope_id,
                target_subject_id=target_subject_id,
                target_kc_id=target_kc_id,
                purpose=purpose,
                status="active",
                created_at=_now(),
                expires_at=expires_at,
            )
            payload["grants"].append(self._grant_record(grant))
            self._save_receipt(
                payload,
                operation_id=operation_id,
                action="create_grant",
                resource_id=grant.grant_id,
            )
            self._save(payload)
            return grant

    def revoke_grant(self, grant_id: str) -> MemoryGrant:
        with self._locked() as payload:
            grant = self._owned_grant(payload, grant_id)
            if grant.status == "revoked":
                return grant
            updated = grant.model_copy(update={"status": "revoked", "revoked_at": _now()})
            record = next(
                row
                for row in payload["grants"]
                if row.get("owner_id") == self.owner_id and row.get("grant_id") == grant_id
            )
            record.clear()
            record.update(self._grant_record(updated))
            self._save(payload)
            return updated

    def list_grants(
        self,
        *,
        active_only: bool = False,
        at_time: str | None = None,
    ) -> list[MemoryGrant]:
        grants = [
            MemoryGrant.model_validate(record)
            for record in self._load()["grants"]
            if record.get("owner_id") == self.owner_id
        ]
        if active_only:
            grants = [grant for grant in grants if self._grant_is_active(grant, at_time=at_time)]
        return grants

    @staticmethod
    def _grant_is_active(grant: MemoryGrant, *, at_time: str | None = None) -> bool:
        if grant.status != "active":
            return False
        if grant.expires_at is None:
            return True
        observed_at = datetime.fromisoformat((at_time or _now()).replace("Z", "+00:00"))
        expires_at = datetime.fromisoformat(grant.expires_at.replace("Z", "+00:00"))
        return observed_at < expires_at

    def grants_for_request(
        self,
        *,
        requesting_scope: MemoryScope,
        requesting_scope_id: str | None,
        requesting_subject_id: str | None,
        requesting_kc_id: str | None,
        purpose: str,
        at_time: str | None = None,
    ) -> tuple[MemoryGrant, ...]:
        """Return only this owner's currently effective exact request grants."""
        return tuple(
            grant
            for grant in self.list_grants(active_only=True, at_time=at_time)
            if (
                grant.requesting_scope,
                grant.requesting_scope_id,
                grant.requesting_subject_id,
                grant.requesting_kc_id,
                grant.purpose,
            )
            == (
                requesting_scope,
                requesting_scope_id,
                requesting_subject_id,
                requesting_kc_id,
                purpose,
            )
        )

    def grant_authorizes(
        self,
        grant_id: str,
        *,
        requesting_scope: MemoryScope,
        requesting_scope_id: str | None,
        requesting_subject_id: str | None,
        requesting_kc_id: str | None,
        target_scope: MemoryScope,
        target_scope_id: str | None,
        target_subject_id: str | None,
        target_kc_id: str | None,
        purpose: str,
        at_time: str | None = None,
    ) -> bool:
        try:
            grant = self._owned_grant(self._load(), grant_id)
        except KeyError:
            return False
        return self._grant_is_active(grant, at_time=at_time) and (
            grant.requesting_scope,
            grant.requesting_scope_id,
            grant.requesting_subject_id,
            grant.requesting_kc_id,
            grant.target_scope,
            grant.target_scope_id,
            grant.target_subject_id,
            grant.target_kc_id,
            grant.purpose,
        ) == (
            requesting_scope,
            requesting_scope_id,
            requesting_subject_id,
            requesting_kc_id,
            target_scope,
            target_scope_id,
            target_subject_id,
            target_kc_id,
            purpose,
        )

    def search_with_grant(
        self,
        grant_id: str,
        *,
        requesting_scope: MemoryScope,
        requesting_scope_id: str | None,
        requesting_subject_id: str | None,
        requesting_kc_id: str | None,
        purpose: str,
        at_time: str | None = None,
    ) -> list[UserMemoryItem]:
        """Revalidate one exact grant and read only its target partition."""
        with self._locked() as payload:
            try:
                grant = self._owned_grant(payload, grant_id)
            except KeyError as exc:
                raise MemoryAuthorizationError("cross-scope read requires an active grant") from exc
            if not self._grant_is_active(grant, at_time=at_time) or (
                grant.requesting_scope,
                grant.requesting_scope_id,
                grant.requesting_subject_id,
                grant.requesting_kc_id,
                grant.purpose,
            ) != (
                requesting_scope,
                requesting_scope_id,
                requesting_subject_id,
                requesting_kc_id,
                purpose,
            ):
                raise MemoryAuthorizationError("cross-scope read requires an active grant")
            return self._search_active_items(
                scope=grant.target_scope,
                scope_id=grant.target_scope_id,
                subject_id=grant.target_subject_id,
                kc_id=grant.target_kc_id,
                at_time=at_time,
                payload=payload,
            )

    def _owned_grant(self, payload: dict[str, Any], grant_id: str) -> MemoryGrant:
        record = next(
            (
                row
                for row in payload["grants"]
                if row.get("owner_id") == self.owner_id and row.get("grant_id") == grant_id
            ),
            None,
        )
        if record is None:
            raise KeyError(grant_id)
        return MemoryGrant.model_validate(record)

    @staticmethod
    def _grant_record(grant: MemoryGrant) -> dict[str, Any]:
        record = grant.model_dump(mode="json")
        record["expires_at"] = grant.expires_at
        return record

    def authorized_candidates(
        self,
        *,
        scope: MemoryScope | None = None,
        scope_id: str | None = None,
        subject_id: str | None = None,
        kc_id: str | None = None,
        at_time: str | None = None,
    ) -> list[UserMemoryItem]:
        """Return owner-local active candidates before relevance ranking."""
        return self._search_active_items(
            scope=scope,
            scope_id=scope_id,
            subject_id=subject_id,
            kc_id=kc_id,
            at_time=at_time,
        )

    def search(
        self,
        *,
        scope: MemoryScope | None = None,
        scope_id: str | None = None,
        subject_id: str | None = None,
        kc_id: str | None = None,
        keyword: str | None = None,
        vector_query: tuple[float, ...] | None = None,
        requesting_scope: MemoryScope | None = None,
        requesting_scope_id: str | None = None,
        requesting_subject_id: str | None = None,
        requesting_kc_id: str | None = None,
        cross_scope_authorized: bool = False,
        snapshot_id: str | None = None,
        purpose: str = "memory_retrieval",
        at_time: str | None = None,
        limit: int = MAX_MEMORY_RESULTS,
        token_budget: int = MAX_MEMORY_TOKEN_BUDGET,
    ) -> list[UserMemoryItem]:
        """Run bounded retrieval while preserving the historical list return type."""
        result = self.search_hybrid(
            scope=scope,
            scope_id=scope_id,
            subject_id=subject_id,
            kc_id=kc_id,
            keyword=keyword,
            vector_query=vector_query,
            requesting_scope=requesting_scope,
            requesting_scope_id=requesting_scope_id,
            requesting_subject_id=requesting_subject_id,
            requesting_kc_id=requesting_kc_id,
            cross_scope_authorized=cross_scope_authorized,
            snapshot_id=snapshot_id,
            purpose=purpose,
            at_time=at_time,
            limit=limit,
            token_budget=token_budget,
        )
        return list(result.items)

    def search_hybrid(
        self,
        *,
        scope: MemoryScope | None = None,
        scope_id: str | None = None,
        subject_id: str | None = None,
        kc_id: str | None = None,
        keyword: str | None = None,
        vector_query: tuple[float, ...] | None = None,
        requesting_scope: MemoryScope | None = None,
        requesting_scope_id: str | None = None,
        requesting_subject_id: str | None = None,
        requesting_kc_id: str | None = None,
        cross_scope_authorized: bool = False,
        snapshot_id: str | None = None,
        purpose: str = "memory_retrieval",
        at_time: str | None = None,
        limit: int = MAX_MEMORY_RESULTS,
        token_budget: int = MAX_MEMORY_TOKEN_BUDGET,
    ) -> MemoryHybridSearchResult:
        """Authorize first, then perform lexical/vector retrieval and audit results."""
        if requesting_scope is not None and scope is None:
            raise MemoryAuthorizationError("contextual retrieval requires an explicit target scope")
        crosses_scope = requesting_scope is not None and (
            scope not in (None, requesting_scope)
            or (
                scope == requesting_scope
                and requesting_scope_id is not None
                and scope_id != requesting_scope_id
            )
            or (requesting_subject_id is not None and subject_id != requesting_subject_id)
            or (scope == "subject" and requesting_kc_id is not None and kc_id != requesting_kc_id)
        )
        if crosses_scope:
            if not cross_scope_authorized:
                raise MemoryAuthorizationError(
                    "cross-scope memory read requires user authorization"
                )
            if snapshot_id is None:
                raise ValueError("authorized cross-scope reads require snapshot_id for audit")
        candidates = self.authorized_candidates(
            scope=scope,
            scope_id=scope_id,
            subject_id=subject_id,
            kc_id=kc_id,
            at_time=at_time,
        )
        result = self.rank_candidates(
            candidates,
            keyword=keyword,
            vector_query=vector_query,
            enable_vector=vector_query is not None,
            limit=limit,
            token_budget=token_budget,
        )
        if snapshot_id is not None:
            envelopes = [
                {
                    "owner_id": self.owner_id,
                    "record": MemoryAccessRecord(
                        record_id=f"mar_{uuid4().hex[:16]}",
                        snapshot_id=snapshot_id,
                        created_at=_now(),
                        scope=(
                            f"{item.scope}:{item.scope_id or '*'}:"
                            f"{item.subject_id or '*'}:{item.kc_id or '*'}"
                        ),
                        key=item.memory_id,
                        version_read=item.memory_id,
                        purpose=purpose,
                        user_authorized=True,
                    ).model_dump(mode="json"),
                }
                for item in result.items
            ]
            if envelopes:
                # Pure append with a bounded tail: the old path took the
                # exclusive lock, loaded EVERY section, appended, and
                # rewrote the entire store — quadratic cumulative IO as the
                # audit trail grew, on every audited read.
                self._adapter.append_records(
                    "access_records", envelopes, keep_newest=MAX_ACCESS_RECORDS
                )
        return result

    def rank_candidates(
        self,
        candidates: Sequence[UserMemoryItem],
        *,
        keyword: str | None,
        vector_query: tuple[float, ...] | None = None,
        enable_vector: bool = False,
        limit: int = MAX_MEMORY_RESULTS,
        token_budget: int = MAX_MEMORY_TOKEN_BUDGET,
    ) -> MemoryHybridSearchResult:
        """Rank a candidate set that was already admitted by scope/grant checks.

        Owner filtering is repeated before index access. This post-filter means
        even a buggy caller cannot expose another owner's claim to embeddings.
        """
        allowed = [item for item in candidates if item.owner_id == self.owner_id]
        allowed_ids = {item.memory_id for item in allowed}
        reasons: list[str] = []
        resolved_vector = vector_query
        vector_scores: dict[str, float] | None = None
        if enable_vector or resolved_vector is not None:
            try:
                if resolved_vector is None:
                    query = (keyword or "").strip()
                    if not query:
                        raise ValueError("vector retrieval requires a query")
                    vectors = list(self._embed_texts([query]))
                    if len(vectors) != 1:
                        raise ValueError("embedding provider returned an unexpected query count")
                    resolved_vector = tuple(float(value) for value in vectors[0])
                if self._index_store is None:
                    reasons.append("canonical_memory_vector_index_unavailable")
                else:
                    vector_scores = self._index_store.vector_scores(
                        resolved_vector,
                        allowed_memory_ids=allowed_ids,
                        embed_claims=self._embed_texts,
                    )
                    if not vector_scores:
                        reasons.append("canonical_memory_vector_index_unavailable")
                        vector_scores = None
            except Exception:
                # Do not log the query or provider payload. The caller receives
                # a stable code and lexical retrieval remains available.
                reasons.append("canonical_memory_vector_failed")
                vector_scores = None
        return rank_memory_candidates(
            allowed,
            keyword=keyword,
            vector_scores=vector_scores,
            limit=limit,
            token_budget=token_budget,
            degradation_reasons=reasons,
        )

    def _search_active_items(
        self,
        *,
        scope: MemoryScope | None = None,
        scope_id: str | None = None,
        subject_id: str | None = None,
        kc_id: str | None = None,
        keyword: str | None = None,
        at_time: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> list[UserMemoryItem]:
        """Filter owner-local canonical truth after the caller's authorization gate."""
        needle = (keyword or "").casefold()
        effective_time = at_time or _now()
        current = payload if payload is not None else self._load()
        results = [
            UserMemoryItem.model_validate(item)
            for item in current["items"]
            if item.get("owner_id") == self.owner_id
            and item.get("status") == "active"
            and str(item.get("valid_from", "")) <= effective_time
            and (item.get("valid_until") is None or effective_time < str(item.get("valid_until")))
            and (scope is None or item.get("scope") == scope)
            and (scope_id is None or item.get("scope_id") == scope_id)
            and (subject_id is None or item.get("subject_id") == subject_id)
            and (kc_id is None or item.get("kc_id") == kc_id)
            and (
                not needle or needle in f"{item.get('key', '')} {item.get('value', '')}".casefold()
            )
        ]
        return sorted(
            results,
            key=lambda item: (
                item.scope,
                item.scope_id or "",
                item.subject_id or "",
                item.kc_id or "",
                item.key,
                item.valid_from,
            ),
        )

    def record_accesses(
        self,
        *,
        snapshot_id: str,
        items: Iterable[UserMemoryItem],
        purpose: str,
        created_at: str | None = None,
        user_authorized: bool = True,
    ) -> list[MemoryAccessRecord]:
        """Durably record a bounded, owner-local context read once.

        Context assembly needs to select active records before it can derive a
        frozen ``snapshot_id``.  This explicit second step avoids a second
        retrieval and records the exact ids/versions that informed that
        snapshot.  It deliberately accepts item objects (not arbitrary ids),
        stores no memory value, and is idempotent for a replay of the same
        snapshot and item version.
        """
        if not snapshot_id:
            raise ValueError("snapshot_id is required for memory access audit")
        if not purpose.strip():
            raise ValueError("purpose is required for memory access audit")

        read_at = created_at or _now()
        unique_items = {
            (item.memory_id, item.updated_at): item
            for item in items
            if item.owner_id == self.owner_id
        }
        records: list[MemoryAccessRecord] = []
        with self._locked() as payload:
            existing_ids = {
                str(envelope.get("record", {}).get("record_id"))
                for envelope in payload["access_records"]
            }
            changed = False
            for memory_id, version in sorted(unique_items):
                item = unique_items[(memory_id, version)]
                seed = f"{self.owner_id}:{snapshot_id}:{memory_id}:{version}:{purpose}"
                record = MemoryAccessRecord(
                    record_id=f"mar_{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:20]}",
                    snapshot_id=snapshot_id,
                    created_at=read_at,
                    scope=(
                        f"canonical_memory:{item.scope}:{item.scope_id or '*'}:"
                        f"{item.subject_id or '*'}:{item.kc_id or '*'}"
                    ),
                    key=item.memory_id,
                    version_read=item.updated_at,
                    purpose=purpose,
                    user_authorized=user_authorized,
                )
                records.append(record)
                if record.record_id in existing_ids:
                    continue
                payload["access_records"].append(
                    {
                        "owner_id": self.owner_id,
                        "record": record.model_dump(mode="json"),
                    }
                )
                changed = True
            if changed:
                self._save(payload)
        return records

    def record_external_accesses(self, records: Iterable[MemoryAccessRecord]) -> None:
        """Durably append cross-domain read audit records (Why Drawer).

        The context assembler's own reads (learner profile, tutor persona,
        subject state, concept signals) previously landed only in a
        process-local log and vanished on restart; invariant 7 requires the
        audit trail to be durable. Canonical memory items keep using
        :meth:`record_accesses` (their ids/versions are item-shaped); this
        method takes pre-built records so no second retrieval is needed.

        Idempotent: the section's synthetic content-hash id means a replay
        of the same record lands on the same row.
        """
        envelopes = [
            {"owner_id": self.owner_id, "record": record.model_dump(mode="json")}
            for record in records
        ]
        if envelopes:
            self._adapter.append_records(
                "access_records", envelopes, keep_newest=MAX_ACCESS_RECORDS
            )

    def history(
        self,
        scope: MemoryScope,
        key: str,
        *,
        scope_id: str | None = None,
        subject_id: str | None = None,
        kc_id: str | None = None,
    ) -> list[UserMemoryItem]:
        records = [
            UserMemoryItem.model_validate(item)
            for item in self._load()["items"]
            if item.get("owner_id") == self.owner_id
            and item.get("scope") == scope
            and item.get("scope_id") == scope_id
            and item.get("subject_id") == subject_id
            and item.get("kc_id") == kc_id
            and item.get("key") == key
        ]
        return sorted(records, key=lambda item: item.created_at)

    def conflicts(
        self,
        *,
        scope: MemoryScope | None = None,
        scope_id: str | None = None,
        subject_id: str | None = None,
        kc_id: str | None = None,
    ) -> list[MemoryConflict]:
        payload = self._load()
        active = {
            item.memory_id: item
            for item in (UserMemoryItem.model_validate(record) for record in payload["items"])
            if item.owner_id == self.owner_id and item.status == "active"
        }
        results: list[MemoryConflict] = []
        for candidate in (
            MemoryCandidate.model_validate(record) for record in payload["candidates"]
        ):
            if candidate.owner_id != self.owner_id or candidate.status != "conflict":
                continue
            if scope is not None and candidate.scope != scope:
                continue
            if scope_id is not None and candidate.scope_id != scope_id:
                continue
            if subject_id is not None and candidate.subject_id != subject_id:
                continue
            if kc_id is not None and candidate.kc_id != kc_id:
                continue
            conflicting = [
                active[memory_id]
                for memory_id in candidate.conflict_memory_ids
                if memory_id in active
            ]
            if conflicting:
                results.append(
                    MemoryConflict(
                        scope=candidate.scope,
                        scope_id=candidate.scope_id,
                        subject_id=candidate.subject_id,
                        kc_id=candidate.kc_id,
                        key=candidate.key,
                        candidate_id=candidate.candidate_id,
                        candidate_value=candidate.value,
                        memory_ids=tuple(item.memory_id for item in conflicting),
                        values=tuple(item.value for item in conflicting),
                    )
                )
        return results

    def lifecycle(self, memory_id: str | None = None) -> list[MemoryLifecycleRecord]:
        return [
            MemoryLifecycleRecord.model_validate(record)
            for record in self._load()["lifecycle"]
            if record.get("owner_id") == self.owner_id
            and (memory_id is None or record.get("memory_id") == memory_id)
        ]

    def list_access_records(self, snapshot_id: str | None = None) -> list[MemoryAccessRecord]:
        """Return owner-authorized audit rows for the Why Drawer."""
        return [
            MemoryAccessRecord.model_validate(envelope["record"])
            for envelope in self._load()["access_records"]
            if envelope.get("owner_id") == self.owner_id
            and (
                snapshot_id is None or envelope.get("record", {}).get("snapshot_id") == snapshot_id
            )
        ]
