"""Misconception hypotheses (F-10): one error must not become a stable misconception.

A ``MisconceptionHypothesis`` requires a rubric reference and a described error
pattern; it starts as a *candidate* and only confirms on repeated independent
evidence (or explicit user confirmation). This encodes the WS-10 acceptance
"一次错误不成本稳定误区" — repairing a confirmed misconception never deletes the
original error record (it links via evidence_refs), keeping the history
auditable (invariant #1 / §11.1).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
import sqlite3
from threading import RLock
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from traittutor.services.path_service import PathService
from traittutor.unified_storage import SectionedRecordStore

MisconceptionStatus = Literal["candidate", "confirmed", "resolved"]

# A single error observation must not confirm a misconception; it takes at
# least this many independent evidence refs (or explicit user confirmation).
MISCONCEPTION_EVIDENCE_THRESHOLD = 2


class MisconceptionHypothesis(BaseModel):
    """A rubric-grounded, pattern-described error hypothesis for a KC set."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    hypothesis_id: str = Field(min_length=1, max_length=96)
    user_id: str = Field(min_length=1, max_length=96)
    subject_id: str = Field(min_length=1, max_length=96)
    kc_ids: tuple[str, ...] = Field(min_length=1, max_length=24)
    # A rubric reference is required: a bare "got it wrong" is not a pattern.
    rubric_ref: str = Field(min_length=1, max_length=200)
    pattern: str = Field(min_length=1, max_length=400)
    evidence_refs: tuple[str, ...] = Field(default_factory=tuple, max_length=24)
    status: MisconceptionStatus = "candidate"
    created_at: str
    updated_at: str


class MisconceptionConfirmationError(ValueError):
    """Raised when a confirmation lacks the evidence/user gate."""


class MisconceptionStoreError(RuntimeError):
    """The durable store cannot be read without weakening owner isolation."""


class MisconceptionStore:
    """Candidate -> confirmed -> resolved lifecycle.

    ``MisconceptionStore()`` preserves the original process-local behavior for
    compatibility tests. Production callers opt into durable mode by passing
    both ``path`` and the authenticated ``owner_id``. Each durable mutation is
    guarded by an OS file lock and atomically replaces the JSON snapshot.
    """

    _SCHEMA_VERSION = 1

    def __init__(
        self,
        path: Path | None = None,
        *,
        owner_id: str | None = None,
        path_service: PathService | None = None,
    ) -> None:
        if path is not None and not owner_id:
            raise ValueError("durable misconception stores require owner_id")
        if owner_id is not None and not owner_id.strip():
            raise ValueError("owner_id must not be blank")
        self._path = Path(path) if path is not None else None
        self._owner_id = owner_id.strip() if owner_id is not None else None
        self._items: dict[str, MisconceptionHypothesis] = {}
        self._lock = RLock()
        self._adapter = (
            SectionedRecordStore(
                "misconceptions",
                self._owner_id or "in-memory",
                schema_version=self._SCHEMA_VERSION,
                path_service=path_service,
                legacy_path=path,
            )
            if path is not None
            else None
        )
        self._active_conn: sqlite3.Connection | None = None
        if self._path is not None:
            with self._lock, self._file_lock():
                self._load_unlocked()

    @property
    def owner_id(self) -> str | None:
        return self._owner_id

    @property
    def path(self) -> Path | None:
        return self._path

    @contextmanager
    def _file_lock(self) -> Iterator[None]:
        if self._path is None:
            yield
            return
        assert self._adapter is not None
        with self._adapter.transaction() as connection:
            self._active_conn = connection
            try:
                yield
            finally:
                self._active_conn = None

    def _load_unlocked(self) -> None:
        if self._path is None or self._adapter is None:
            return
        try:
            payload = (
                self._adapter.read_via(self._active_conn)
                if self._active_conn is not None
                else self._adapter.snapshot()
            )
            if not isinstance(payload, dict):
                raise ValueError("store root must be an object")
            if payload.get("schema_version") != self._SCHEMA_VERSION:
                raise ValueError("unsupported misconception store schema")
            raw_items = payload.get("items", [])
            if not isinstance(raw_items, list):
                raise ValueError("items must be a list")
            items = [MisconceptionHypothesis.model_validate(item) for item in raw_items]
            if any(item.user_id != self._owner_id for item in items):
                raise ValueError("misconception item owner mismatch")
            if len({item.hypothesis_id for item in items}) != len(items):
                raise ValueError("duplicate misconception identity")
        except (OSError, sqlite3.Error, ValueError) as exc:
            raise MisconceptionStoreError("misconception store is unreadable") from exc
        self._items = {item.hypothesis_id: item for item in items}

    def _refresh_unlocked(self) -> None:
        if self._path is not None:
            self._load_unlocked()

    def _save_unlocked(self) -> None:
        if self._path is None:
            return
        assert self._adapter is not None
        payload = {
            "schema_version": self._SCHEMA_VERSION,
            "items": [item.model_dump(mode="json") for _, item in sorted(self._items.items())],
        }
        if self._active_conn is not None:
            self._adapter.write_via(self._active_conn, payload)
        else:
            self._adapter.replace_all(payload)

    def _assert_owner(self, user_id: str) -> None:
        if self._owner_id is not None and user_id != self._owner_id:
            raise PermissionError("misconception store owner mismatch")

    def propose(
        self,
        *,
        hypothesis_id: str,
        user_id: str,
        subject_id: str,
        kc_ids: tuple[str, ...],
        rubric_ref: str,
        pattern: str,
        evidence_refs: tuple[str, ...] = (),
        created_at: str,
    ) -> MisconceptionHypothesis:
        self._assert_owner(user_id)
        hypothesis = MisconceptionHypothesis(
            hypothesis_id=hypothesis_id,
            user_id=user_id,
            subject_id=subject_id,
            kc_ids=kc_ids,
            rubric_ref=rubric_ref,
            pattern=pattern,
            evidence_refs=evidence_refs,
            status="candidate",
            created_at=created_at,
            updated_at=created_at,
        )
        with self._lock, self._file_lock():
            self._refresh_unlocked()
            existing = self._items.get(hypothesis_id)
            if existing is not None and existing.user_id != user_id:
                raise PermissionError("misconception owner mismatch")
            self._items[hypothesis_id] = hypothesis
            self._save_unlocked()
        return hypothesis

    def add_evidence(
        self, hypothesis_id: str, evidence_ref: str, *, now: str
    ) -> MisconceptionHypothesis:
        # Read, deduplicate, and replace atomically so distinct concurrent
        # evidence refs cannot overwrite one another's frozen-model copy.
        with self._lock, self._file_lock():
            self._refresh_unlocked()
            item = self._require_unlocked(hypothesis_id)
            if evidence_ref in item.evidence_refs:
                return item
            updated = item.model_copy(
                update={
                    "evidence_refs": item.evidence_refs + (evidence_ref,),
                    "updated_at": now,
                }
            )
            self._items[hypothesis_id] = updated
            self._save_unlocked()
            return updated

    def confirm(
        self,
        hypothesis_id: str,
        *,
        confirmed_by_user: bool = False,
        now: str,
    ) -> MisconceptionHypothesis:
        with self._lock, self._file_lock():
            self._refresh_unlocked()
            item = self._require_unlocked(hypothesis_id)
            if not confirmed_by_user and len(item.evidence_refs) < MISCONCEPTION_EVIDENCE_THRESHOLD:
                raise MisconceptionConfirmationError(
                    "a misconception requires repeated evidence or explicit user confirmation"
                )
            updated = item.model_copy(update={"status": "confirmed", "updated_at": now})
            self._items[hypothesis_id] = updated
            self._save_unlocked()
            return updated

    def resolve(self, hypothesis_id: str, *, now: str) -> MisconceptionHypothesis:
        # Resolved, never deleted: the original error evidence stays linked.
        with self._lock, self._file_lock():
            self._refresh_unlocked()
            item = self._require_unlocked(hypothesis_id)
            updated = item.model_copy(update={"status": "resolved", "updated_at": now})
            self._items[hypothesis_id] = updated
            self._save_unlocked()
            return updated

    def get(self, hypothesis_id: str) -> MisconceptionHypothesis | None:
        with self._lock, self._file_lock():
            self._refresh_unlocked()
            item = self._items.get(hypothesis_id)
            if item is not None:
                self._assert_owner(item.user_id)
            return item

    def list_for(
        self,
        *,
        user_id: str,
        subject_id: str,
        kc_id: str | None = None,
    ) -> list[MisconceptionHypothesis]:
        """List only one authenticated owner/subject partition."""
        self._assert_owner(user_id)
        with self._lock, self._file_lock():
            self._refresh_unlocked()
            items = [
                item
                for item in self._items.values()
                if item.user_id == user_id
                and item.subject_id == subject_id
                and (kc_id is None or kc_id in item.kc_ids)
            ]
        return sorted(items, key=lambda item: (item.created_at, item.hypothesis_id))

    def list_subject_ids(self, *, user_id: str) -> tuple[str, ...]:
        """Enumerate this owner's existing subject partitions without exposing items."""
        self._assert_owner(user_id)
        with self._lock, self._file_lock():
            self._refresh_unlocked()
            subject_ids = {
                item.subject_id
                for item in self._items.values()
                if item.user_id == user_id and item.subject_id.strip()
            }
        return tuple(sorted(subject_ids))

    def _require_unlocked(self, hypothesis_id: str) -> MisconceptionHypothesis:
        item = self._items.get(hypothesis_id)
        if item is None:
            raise KeyError(hypothesis_id)
        self._assert_owner(item.user_id)
        return item
