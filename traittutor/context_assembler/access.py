"""Auditable, in-memory records of cross-partition context reads."""

from __future__ import annotations

from collections import defaultdict
from threading import Lock

from pydantic import BaseModel, ConfigDict, field_validator

from .snapshot import _require_utc_iso


class MemoryAccessRecord(BaseModel):
    """Explain why one versioned memory or learner-state item was consulted.

    The record stores identifiers and authorization state, not the recalled
    content.  This makes cross-partition use inspectable without turning the
    audit trail itself into an ungoverned copy of private learner memory.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    record_id: str
    snapshot_id: str
    created_at: str
    scope: str
    key: str
    version_read: str | None = None
    purpose: str
    user_authorized: bool

    _validate_created_at = field_validator("created_at")(_require_utc_iso)


class MemoryAccessLog:
    """Small process-local access log with idempotent append semantics.

    WS-1 needs a deterministic audit seam before the durable v2.7 memory
    lifecycle exists.  A lock keeps concurrent assistant turns consistent,
    while record-id deduplication makes retries safe.  Persistence can replace
    this implementation later without changing the snapshot contract.
    """

    def __init__(self) -> None:
        self._records: dict[str, MemoryAccessRecord] = {}
        self._snapshot_record_ids: dict[str, list[str]] = defaultdict(list)
        self._lock = Lock()

    def append(self, record: MemoryAccessRecord) -> None:
        """Append ``record`` once; replaying its id is an intentional no-op."""
        with self._lock:
            if record.record_id in self._records:
                return
            self._records[record.record_id] = record
            self._snapshot_record_ids[record.snapshot_id].append(record.record_id)

    def for_snapshot(self, snapshot_id: str) -> list[MemoryAccessRecord]:
        """Return the insertion-ordered records for one immutable snapshot."""
        with self._lock:
            return [
                self._records[record_id]
                for record_id in self._snapshot_record_ids.get(snapshot_id, ())
            ]
