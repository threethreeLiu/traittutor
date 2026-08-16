"""Immutable learner-event ledger (F-09, invariants #1/#2/#4/#6).

The ledger is the append-only source of truth for what a learner did. Every
valid answer becomes an immutable ``LearnerEvent`` *before* any derived state
(BKT / error record / review / support strategy) is recomputed (invariant #1:
先事件、后派生). Replaying the same event_id or idempotency_key must never
duplicate state or double-count — ``append`` returns ``"duplicate"`` and writes
nothing (invariant #4).

Only ``strong`` evidence (server-graded, valid item, reliable KC attribution)
may later update BKT. Exposure signals — reading, bookmarking, asking,
searching, dwelling, self-report — are recorded here as ``evidence_strength !=
"strong"`` so they remain visible to the system while being *barred* from BKT
(invariant #2). Unreliable attribution is tagged ``attribution_pending`` /
``weak`` rather than dropped, so the raw event survives even when it cannot
safely update mastery.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
import hashlib
from pathlib import Path
import secrets
import sqlite3
from threading import Lock, RLock
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from traittutor.multi_user.models import LOCAL_ADMIN_ID
from traittutor.services.path_service import PathService
from traittutor.unified_storage import SectionedRecordStore

EvidenceStrength = Literal["strong", "exposure", "none"]
AttributionStatus = Literal["reliable", "attribution_pending", "weak"]
SurfaceType = Literal["quiz", "practice", "chat", "research", "reading", "review"]

AppendOutcome = Literal["appended", "duplicate"]
DerivedOutcome = Literal["applied", "already_applied", "queued", "claim_lost"]
AmendmentAction = Literal["void"]
AmendmentReason = Literal[
    "grading_error",
    "item_invalid",
    "attribution_error",
    "duplicate_evidence",
    "privacy_request",
]


def stable_amendment_identity(*, user_id: str, target_event_id: str) -> tuple[str, str]:
    """Derive the one retry-safe void identity for one owner-held event."""
    owner = user_id.strip()
    target = target_event_id.strip()
    if not owner or not target:
        raise ValueError("user_id and target_event_id are required")
    digest = hashlib.sha256(f"{owner}\x1f{target}\x1fvoid".encode()).hexdigest()
    return f"amend-{digest[:48]}", f"event-void:{digest}"


class LearnerEvent(BaseModel):
    """One immutable learning event on the canonical ledger."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str = Field(min_length=1, max_length=96)
    # Stable key over (user, subject, surface, content) so a replayed submit of
    # the *same* answer is deduped even if event_id is regenerated. Required:
    # the caller must derive it deterministically (invariant #4).
    idempotency_key: str = Field(min_length=1, max_length=160)
    user_id: str = Field(min_length=1, max_length=96)
    subject_id: str | None = None
    kc_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=24)
    surface_type: SurfaceType
    thread_id: str | None = None
    research_run_id: str | None = None
    page_id: str | None = None
    learning_path_id: str | None = None
    module_id: str | None = None
    item_id: str | None = None
    error_tag: str | None = None
    # None for non-graded surfaces (chat/reading/research). A present value is
    # necessary but NOT sufficient for strong evidence — see is_strong_evidence.
    answer_correct: bool | None = None
    evidence_strength: EvidenceStrength
    attribution_status: AttributionStatus = "reliable"
    created_at: str


class LearnerEventAmendment(BaseModel):
    """Immutable correction which supersedes one canonical event.

    This is deliberately an amendment, not an editable field on
    :class:`LearnerEvent`: audit/replay can still see the original server
    verdict, while every mastery projection consumes only the effective event
    stream.  ``reason_code`` is a bounded operational code rather than free
    text so an audit record cannot become an accidental answer/prompt store.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    amendment_id: str = Field(min_length=1, max_length=96)
    idempotency_key: str = Field(min_length=1, max_length=160)
    target_event_id: str = Field(min_length=1, max_length=96)
    user_id: str = Field(min_length=1, max_length=96)
    subject_id: str | None = None
    kc_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=24)
    action: AmendmentAction = "void"
    reason_code: AmendmentReason
    created_at: str


class DerivedFailure(BaseModel):
    """Durable retry item for one event-derived projection.

    A row with ``attempts == 0`` is planned work persisted atomically with the
    source event.  This closes the crash window between appending an event and
    starting its first derived update.  Failed attempts retain only a bounded
    error description; the immutable event remains the replay source.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str = Field(min_length=1, max_length=96)
    operation: str = Field(min_length=1, max_length=96)
    attempts: int = Field(default=0, ge=0)
    last_error: str = Field(default="", max_length=500)
    queued_at: str
    updated_at: str
    # Optional fields keep schema-v1 snapshots readable while adding a durable
    # cross-process reservation. A new token replaces an expired one; only the
    # current token may complete or release the row (fencing).
    claim_token: str | None = Field(default=None, min_length=16, max_length=96)
    lease_expires_at: str | None = None


class DerivedClaim(BaseModel):
    """One durable lease over a queued derived operation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str = Field(min_length=1, max_length=96)
    operation: str = Field(min_length=1, max_length=96)
    token: str = Field(min_length=16, max_length=96)
    lease_expires_at: str


class LearnerEventLedgerError(RuntimeError):
    """The durable event ledger cannot be read or safely updated."""


def is_strong_evidence(event: LearnerEvent) -> bool:
    """True ONLY for server-graded, valid, reliably-attributed evidence.

    This is the single gate that keeps BKT honest (invariant #2): questions,
    searches, reading, bookmarks, dwell, and self-report never qualify, and
    items with pending/weak attribution are excluded even when graded.
    """
    return (
        event.evidence_strength == "strong"
        and event.attribution_status == "reliable"
        and event.answer_correct is not None
    )


class LearnerEventLedger:
    """Append-only event ledger with durable replay and derived-work markers.

    When ``path`` is provided, every read-modify-write runs in one ``BEGIN
    IMMEDIATE`` transaction over the owner-bound unified database (replacing the
    legacy OS file lock + JSON snapshot).  ``replay`` is intentionally a pure
    projection hook: passing it to the constructor rebuilds derived state from
    all immutable events immediately after load.

    Derived operations are registered in the same atomic snapshot as a newly
    appended event.  An operation is removed from the retry queue only after
    its callback succeeds, and a durable completion marker prevents a replay
    from applying it twice.  Callbacks that write another store must also use
    ``event_id`` idempotency because no filesystem can atomically commit two
    independent stores across a process crash.
    """

    _SCHEMA_VERSION = 2

    def __init__(
        self,
        path: Path | None = None,
        *,
        replay: Callable[[LearnerEvent], None] | None = None,
        path_service: PathService | None = None,
        db_path: Any | None = None,
    ) -> None:
        self._path = Path(path) if path is not None else None
        self._events: dict[str, LearnerEvent] = {}
        self._idem_keys: set[str] = set()
        self._amendments: dict[str, LearnerEventAmendment] = {}
        self._amendment_idem_keys: set[str] = set()
        self._voided_event_ids: set[str] = set()
        self._derived_applied: set[str] = set()
        self._derived_queue: dict[str, DerivedFailure] = {}
        self._lock = RLock()
        self._operation_locks: dict[str, Lock] = {}
        # Unified-DB persistence seam (Phase 5 task 10). ``path`` is retained
        # only as the "persistence configured" flag (``None`` == an in-memory
        # ledger); the records themselves live in the owner-bound unified
        # database. A legacy ``path=`` constructor still isolates to its own
        # database location (the path's parent dir) so existing tests keep
        # working unchanged, while production threads ``path_service`` so the
        # ledger resolves to the canonical workspace database where the Phase 4
        # migration landed the historical events.
        self._adapter = SectionedRecordStore(
            "learner_events",
            LOCAL_ADMIN_ID,
            schema_version=self._SCHEMA_VERSION,
            path_service=path_service,
            db_path=db_path,
            legacy_path=path,
        )
        self._active_conn: sqlite3.Connection | None = None
        if self._path is not None:
            with self._lock, self._file_lock():
                self._load_unlocked()
        if replay is not None:
            self.replay(replay)

    @property
    def path(self) -> Path | None:
        return self._path

    @staticmethod
    def _derived_key(event_id: str, operation: str) -> str:
        return f"{event_id}\x1f{operation}"

    @staticmethod
    def amendment_reconciliation_operation(amendment_id: str) -> str:
        """Stable durable outbox key for one amendment's external reducers."""
        return f"amendment-reconcile:{amendment_id}"

    @contextmanager
    def _file_lock(self) -> Iterator[None]:
        # One ``BEGIN IMMEDIATE`` transaction replaces the legacy fcntl flock:
        # the same mutual exclusion with stronger atomicity (read + mutate +
        # write commit as one unit, or roll back). SQLite's ``busy_timeout``
        # makes a contending writer block — matching flock's blocking
        # semantics, including across processes. A ``None`` path keeps an
        # in-memory ledger with no persistence.
        if self._path is None:
            yield
            return
        with self._adapter.transaction() as connection:
            self._active_conn = connection
            try:
                yield
            finally:
                self._active_conn = None

    def _load_unlocked(self) -> None:
        if self._path is None:
            return
        try:
            payload = (
                self._adapter.read_via(self._active_conn)
                if self._active_conn is not None
                else self._adapter.snapshot()
            )
            if not isinstance(payload, dict):
                raise ValueError("ledger root must be an object")
            events = [LearnerEvent.model_validate(item) for item in payload.get("events", [])]
            amendments = [
                LearnerEventAmendment.model_validate(item) for item in payload.get("amendments", [])
            ]
            queue = [
                DerivedFailure.model_validate(item) for item in payload.get("derived_queue", [])
            ]
            applied = payload.get("derived_applied", [])
            if not isinstance(applied, list) or any(not isinstance(item, str) for item in applied):
                raise ValueError("derived_applied must be a string list")
        except (sqlite3.Error, ValueError) as exc:
            raise LearnerEventLedgerError("learner event ledger is unreadable") from exc

        event_ids = [event.event_id for event in events]
        idem_keys = [event.idempotency_key for event in events]
        if len(event_ids) != len(set(event_ids)) or len(idem_keys) != len(set(idem_keys)):
            raise LearnerEventLedgerError("learner event ledger contains duplicate identities")
        amendment_ids = [amendment.amendment_id for amendment in amendments]
        amendment_idem_keys = [amendment.idempotency_key for amendment in amendments]
        target_ids = [amendment.target_event_id for amendment in amendments]
        if (
            len(amendment_ids) != len(set(amendment_ids))
            or len(amendment_idem_keys) != len(set(amendment_idem_keys))
            or len(target_ids) != len(set(target_ids))
        ):
            raise LearnerEventLedgerError("learner event ledger contains duplicate amendments")
        events_by_id = {event.event_id: event for event in events}
        for amendment in amendments:
            target = events_by_id.get(amendment.target_event_id)
            if target is None or not self._amendment_matches_target(amendment, target):
                raise LearnerEventLedgerError("learner event ledger contains invalid amendment")
        self._events = {event.event_id: event for event in events}
        self._idem_keys = set(idem_keys)
        self._amendments = {amendment.amendment_id: amendment for amendment in amendments}
        self._amendment_idem_keys = set(amendment_idem_keys)
        self._voided_event_ids = set(target_ids)
        self._derived_applied = set(applied)
        self._derived_queue = {
            self._derived_key(item.event_id, item.operation): item for item in queue
        }

    def _project_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self._SCHEMA_VERSION,
            "events": [event.model_dump(mode="json") for event in self._events.values()],
            "amendments": [
                amendment.model_dump(mode="json")
                for _, amendment in sorted(self._amendments.items())
            ],
            "derived_applied": sorted(self._derived_applied),
            "derived_queue": [
                item.model_dump(mode="json") for _, item in sorted(self._derived_queue.items())
            ],
        }

    def _save_unlocked(self) -> None:
        if self._path is None:
            return
        payload = self._project_payload()
        if self._active_conn is not None:
            self._adapter.write_via(self._active_conn, payload)
        else:
            self._adapter.replace_all(payload)

    def _refresh_unlocked(self) -> None:
        if self._path is not None:
            # The unified database is the source of truth; reload within the
            # active transaction so the in-memory model matches the committed
            # snapshot at the start of each locked section.
            self._load_unlocked()

    def append(
        self,
        event: LearnerEvent,
        *,
        derived_operations: Iterable[str] = (),
    ) -> AppendOutcome:
        # Dedup on EITHER identity: a replayed submit must not double-count and
        # must not trigger a second derived update (invariant #4). The check and
        # planned derived writes are one critical, durable section so concurrent
        # retries cannot both observe an absent identity or lose pending work.
        operations = tuple(dict.fromkeys(operation.strip() for operation in derived_operations))
        if any(not operation for operation in operations):
            raise ValueError("derived operation names must not be empty")
        with self._lock, self._file_lock():
            self._refresh_unlocked()
            if event.event_id in self._events or event.idempotency_key in self._idem_keys:
                return "duplicate"
            self._events[event.event_id] = event
            self._idem_keys.add(event.idempotency_key)
            for operation in operations:
                key = self._derived_key(event.event_id, operation)
                self._derived_queue[key] = DerivedFailure(
                    event_id=event.event_id,
                    operation=operation,
                    queued_at=event.created_at,
                    updated_at=event.created_at,
                )
            self._save_unlocked()
            return "appended"

    @staticmethod
    def _amendment_matches_target(
        amendment: LearnerEventAmendment,
        target: LearnerEvent,
    ) -> bool:
        """Validate the immutable partition copied from a server-held target."""
        return (
            amendment.action == "void"
            and amendment.user_id == target.user_id
            and amendment.subject_id == target.subject_id
            and tuple(amendment.kc_ids) == tuple(target.kc_ids)
        )

    def append_amendment(
        self,
        amendment: LearnerEventAmendment,
        *,
        reconciliation_operation: str | None = None,
    ) -> AppendOutcome:
        """Append one owner/subject/KC-bound void without rewriting the event.

        The target's partition is checked while the same durable lock is held.
        A target accepts exactly one void; retrying the same stable identity is
        harmless, while a different amendment for an already voided event is
        rejected rather than silently changing the recorded audit reason.
        """
        operation = reconciliation_operation.strip() if reconciliation_operation else None
        if reconciliation_operation is not None and not operation:
            raise ValueError("derived operation names must not be empty")
        with self._lock, self._file_lock():
            self._refresh_unlocked()
            if (
                amendment.amendment_id in self._amendments
                or amendment.idempotency_key in self._amendment_idem_keys
            ):
                return "duplicate"
            target = self._events.get(amendment.target_event_id)
            if target is None:
                raise KeyError(amendment.target_event_id)
            if not self._amendment_matches_target(amendment, target):
                raise PermissionError("amendment target partition mismatch")
            if amendment.target_event_id in self._voided_event_ids:
                raise ValueError("canonical event already amended")
            self._amendments[amendment.amendment_id] = amendment
            self._amendment_idem_keys.add(amendment.idempotency_key)
            self._voided_event_ids.add(amendment.target_event_id)
            if operation is not None:
                key = self._derived_key(amendment.target_event_id, operation)
                self._derived_queue[key] = DerivedFailure(
                    event_id=amendment.target_event_id,
                    operation=operation,
                    queued_at=amendment.created_at,
                    updated_at=amendment.created_at,
                )
            self._save_unlocked()
            return "appended"

    def apply_derived(
        self,
        event_id: str,
        operation: str,
        callback: Callable[[LearnerEvent], Any],
        *,
        now: str,
        lease_seconds: float = 120.0,
    ) -> DerivedOutcome:
        """Apply one projection once, retaining a retry row on failure.

        A process-local operation lock prevents duplicate callbacks in one
        worker, while a durable lease prevents concurrent callbacks across
        processes. The filesystem lock is deliberately released while the
        callback runs so a projection may inspect the ledger without deadlock.
        Downstream stores must still use ``event_id`` idempotency because a
        crashed worker can execute its callback after its lease is taken over.
        """
        key = self._derived_key(event_id, operation)
        with self._lock:
            operation_lock = self._operation_locks.setdefault(key, Lock())
        with operation_lock:
            claim = self.claim_derived(
                event_id,
                operation,
                now=now,
                lease_seconds=lease_seconds,
            )
            if claim is None:
                with self._lock, self._file_lock():
                    self._refresh_unlocked()
                    return "already_applied" if key in self._derived_applied else "queued"
            event = self.get(event_id)
            if event is None:
                raise KeyError(event_id)
            try:
                callback(event)
            except Exception as exc:
                self.mark_derived_failed(
                    event_id,
                    operation,
                    exc,
                    now=now,
                    claim_token=claim.token,
                )
                return "queued"
            return self.mark_derived_applied(
                event_id,
                operation,
                claim_token=claim.token,
            )

    def claim_derived(
        self,
        event_id: str,
        operation: str,
        *,
        now: str,
        lease_seconds: float = 120.0,
    ) -> DerivedClaim | None:
        """Atomically lease queued work, or return ``None`` while unavailable.

        An expired lease may be taken over with a new token. Completion is a
        compare-and-set against that token, so a stale worker cannot erase the
        replacement worker's queue row or publish a false completion marker.
        """
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        key = self._derived_key(event_id, operation)
        claimed_at = self._parse_timestamp(now)
        expires_at = claimed_at + timedelta(seconds=lease_seconds)
        with self._lock, self._file_lock():
            self._refresh_unlocked()
            if key in self._derived_applied:
                return None
            if event_id not in self._events:
                raise KeyError(event_id)
            queued = self._derived_queue.get(key)
            if queued is None:
                return None
            if (
                queued.claim_token is not None
                and queued.lease_expires_at is not None
                and self._parse_timestamp(queued.lease_expires_at) > claimed_at
            ):
                return None
            token = secrets.token_urlsafe(24)
            lease_expires_at = expires_at.isoformat()
            self._derived_queue[key] = queued.model_copy(
                update={
                    "claim_token": token,
                    "lease_expires_at": lease_expires_at,
                    "updated_at": now,
                }
            )
            self._save_unlocked()
            return DerivedClaim(
                event_id=event_id,
                operation=operation,
                token=token,
                lease_expires_at=lease_expires_at,
            )

    def renew_derived_claim(
        self,
        event_id: str,
        operation: str,
        *,
        claim_token: str,
        now: str,
        lease_seconds: float = 120.0,
    ) -> DerivedClaim | None:
        """Extend one live claim using token-fenced compare-and-set.

        An already expired token cannot be revived, even when no replacement
        worker has claimed the row yet. This keeps the lease boundary honest
        under clock drift and ensures every late worker is fenced consistently.
        """
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        key = self._derived_key(event_id, operation)
        renewed_at = self._parse_timestamp(now)
        lease_expires_at = (renewed_at + timedelta(seconds=lease_seconds)).isoformat()
        with self._lock, self._file_lock():
            self._refresh_unlocked()
            if key in self._derived_applied:
                return None
            if event_id not in self._events:
                raise KeyError(event_id)
            queued = self._derived_queue.get(key)
            if (
                queued is None
                or queued.claim_token != claim_token
                or queued.lease_expires_at is None
                or self._parse_timestamp(queued.lease_expires_at) <= renewed_at
            ):
                return None
            self._derived_queue[key] = queued.model_copy(
                update={
                    "lease_expires_at": lease_expires_at,
                    "updated_at": now,
                }
            )
            self._save_unlocked()
            return DerivedClaim(
                event_id=event_id,
                operation=operation,
                token=claim_token,
                lease_expires_at=lease_expires_at,
            )

    @staticmethod
    def _parse_timestamp(value: str) -> datetime:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    def mark_derived_applied(
        self,
        event_id: str,
        operation: str,
        *,
        claim_token: str | None = None,
    ) -> DerivedOutcome:
        """Complete asynchronously-derived work without executing it again."""
        key = self._derived_key(event_id, operation)
        with self._lock, self._file_lock():
            self._refresh_unlocked()
            if key in self._derived_applied:
                return "already_applied"
            if event_id not in self._events:
                raise KeyError(event_id)
            queued = self._derived_queue.get(key)
            if queued is not None and queued.claim_token != claim_token:
                return "claim_lost"
            if queued is None and claim_token is not None:
                return "claim_lost"
            self._mark_derived_applied_unlocked(event_id, operation)
            return "applied"

    def derived_is_applied(self, event_id: str, operation: str) -> bool:
        """Read a durable projection marker for correction reconciliation."""
        key = self._derived_key(event_id, operation)
        with self._lock, self._file_lock():
            self._refresh_unlocked()
            return key in self._derived_applied

    def _mark_derived_applied_unlocked(self, event_id: str, operation: str) -> None:
        key = self._derived_key(event_id, operation)
        self._derived_applied.add(key)
        self._derived_queue.pop(key, None)
        self._save_unlocked()

    def mark_derived_failed(
        self,
        event_id: str,
        operation: str,
        error: BaseException,
        *,
        now: str,
        claim_token: str | None = None,
    ) -> DerivedOutcome:
        """Persist an asynchronous projection failure for later retry."""
        key = self._derived_key(event_id, operation)
        with self._lock, self._file_lock():
            self._refresh_unlocked()
            event = self._events.get(event_id)
            if event is None:
                raise KeyError(event_id)
            if key in self._derived_applied:
                return "already_applied"
            queued = self._derived_queue.get(key) or DerivedFailure(
                event_id=event_id,
                operation=operation,
                queued_at=now,
                updated_at=now,
            )
            if queued.claim_token != claim_token:
                return "claim_lost"
            self._mark_derived_failed_unlocked(queued, error, now=now)
            return "queued"

    def _mark_derived_failed_unlocked(
        self,
        queued: DerivedFailure,
        error: BaseException,
        *,
        now: str,
    ) -> None:
        key = self._derived_key(queued.event_id, queued.operation)
        self._derived_queue[key] = queued.model_copy(
            update={
                "attempts": queued.attempts + 1,
                "last_error": f"{type(error).__name__}: {error}"[:500],
                "updated_at": now,
                "claim_token": None,
                "lease_expires_at": None,
            }
        )
        self._save_unlocked()

    def get(self, event_id: str) -> LearnerEvent | None:
        with self._lock, self._file_lock():
            self._refresh_unlocked()
            return self._events.get(event_id)

    def retry_failed(
        self,
        handlers: Mapping[str, Callable[[LearnerEvent], None]],
        *,
        now: str,
    ) -> dict[str, DerivedOutcome]:
        """Retry known derived work; missing handlers leave rows untouched."""
        outcomes: dict[str, DerivedOutcome] = {}
        for item in self.pending_derived():
            handler = handlers.get(item.operation)
            if handler is None:
                continue
            key = self._derived_key(item.event_id, item.operation)
            outcomes[key] = self.apply_derived(
                item.event_id,
                item.operation,
                handler,
                now=now,
            )
        return outcomes

    def pending_derived(self) -> list[DerivedFailure]:
        with self._lock, self._file_lock():
            self._refresh_unlocked()
            return sorted(
                self._derived_queue.values(),
                key=lambda item: (item.queued_at, item.event_id, item.operation),
            )

    def replay(self, projector: Callable[[LearnerEvent], None]) -> int:
        """Rebuild from effective immutable evidence, never superseded facts."""
        events = self.effective_events()
        events.sort(key=lambda item: (item.created_at, item.event_id))
        for event in events:
            projector(event)
        return len(events)

    def __iter__(self) -> Iterator[LearnerEvent]:
        with self._lock, self._file_lock():
            self._refresh_unlocked()
            return iter(tuple(self._events.values()))

    def __len__(self) -> int:
        with self._lock, self._file_lock():
            self._refresh_unlocked()
            return len(self._events)

    def events_for(
        self,
        *,
        user_id: str,
        subject_id: str | None = None,
        kc_id: str | None = None,
    ) -> list[LearnerEvent]:
        """Return events for a user, optionally narrowed by subject and KC."""
        with self._lock, self._file_lock():
            self._refresh_unlocked()
            events = tuple(self._events.values())
        results: list[LearnerEvent] = []
        for event in events:
            if event.user_id != user_id:
                continue
            if subject_id is not None and event.subject_id != subject_id:
                continue
            if kc_id is not None and kc_id not in event.kc_ids:
                continue
            results.append(event)
        results.sort(key=lambda item: item.created_at)
        return results

    def amendments_for(
        self,
        *,
        user_id: str,
        subject_id: str | None = None,
        kc_id: str | None = None,
    ) -> list[LearnerEventAmendment]:
        """Return append-only correction audit records inside one partition."""
        with self._lock, self._file_lock():
            self._refresh_unlocked()
            amendments = tuple(self._amendments.values())
        results = [
            amendment
            for amendment in amendments
            if amendment.user_id == user_id
            and (subject_id is None or amendment.subject_id == subject_id)
            and (kc_id is None or kc_id in amendment.kc_ids)
        ]
        return sorted(results, key=lambda item: (item.created_at, item.amendment_id))

    def amendment_for_target(self, target_event_id: str) -> LearnerEventAmendment | None:
        """Resolve the immutable void audit entry without exposing event data."""
        with self._lock, self._file_lock():
            self._refresh_unlocked()
            return next(
                (
                    amendment
                    for amendment in self._amendments.values()
                    if amendment.target_event_id == target_event_id
                ),
                None,
            )

    def is_effective(self, event_or_id: LearnerEvent | str) -> bool:
        """Whether an event may contribute to any derived canonical projection."""
        event_id = event_or_id if isinstance(event_or_id, str) else event_or_id.event_id
        with self._lock, self._file_lock():
            self._refresh_unlocked()
            return event_id in self._events and event_id not in self._voided_event_ids

    def effective_events(self) -> list[LearnerEvent]:
        """Raw immutable facts minus append-only superseded evidence."""
        with self._lock, self._file_lock():
            self._refresh_unlocked()
            return [
                event
                for event_id, event in self._events.items()
                if event_id not in self._voided_event_ids
            ]

    def event_for_identity(self, *, event_id: str, idempotency_key: str) -> LearnerEvent | None:
        """Resolve the canonical event after an idempotent duplicate submit."""
        with self._lock, self._file_lock():
            self._refresh_unlocked()
            direct = self._events.get(event_id)
            if direct is not None:
                return direct
            return next(
                (
                    event
                    for event in self._events.values()
                    if event.idempotency_key == idempotency_key
                ),
                None,
            )

    def strong_evidence_for(
        self,
        *,
        user_id: str,
        subject_id: str | None = None,
        kc_id: str | None = None,
    ) -> list[LearnerEvent]:
        """Strong-evidence subset — the only stream BKT may consume."""
        return [
            event
            for event in self.effective_events()
            if event.user_id == user_id
            and (subject_id is None or event.subject_id == subject_id)
            and (kc_id is None or kc_id in event.kc_ids)
            and is_strong_evidence(event)
        ]
