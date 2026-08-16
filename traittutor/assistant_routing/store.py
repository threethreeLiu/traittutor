"""Owner-bound durable store for :class:`CapabilityDecision` records."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
from pathlib import Path
from typing import Any, Iterator

from pydantic import ValidationError

from traittutor.services.path_service import get_path_service
from traittutor.unified_storage import SectionedRecordStore

from .models import CapabilityDecision

_SCHEMA_VERSION = 1
_COMPLETION_REASONS = {
    "research": "user confirmed and scheduled a research workspace run",
    "learn": "user confirmed and created a learning pack plan",
    "create": "user confirmed and queued a courseware generation task",
}


class CapabilityDecisionStoreError(RuntimeError):
    """The decision ledger could not safely satisfy an operation."""


class CapabilityDecisionIdempotencyConflict(CapabilityDecisionStoreError):
    """One idempotency key was reused with a different route command."""


class CapabilityDecisionNotFound(CapabilityDecisionStoreError):
    """The current owner cannot access the requested decision."""


def _key_hash(value: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > 160:
        raise ValueError("idempotency_key must contain 1 to 160 characters")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class CapabilityDecisionStore:
    """Persist decisions with owner checks and idempotency under one file lock."""

    def __init__(self, owner_id: str, *, path: Path | None = None) -> None:
        owner_id = owner_id.strip()
        if not owner_id:
            raise ValueError("owner_id is required")
        self.owner_id = owner_id
        self._store_path = path
        self._adapter = SectionedRecordStore(
            "capability_decisions",
            owner_id,
            schema_version=_SCHEMA_VERSION,
            path_service=get_path_service() if path is None else None,
            legacy_path=path,
        )

    def _path(self) -> Path:
        return self._store_path or (
            get_path_service().get_workspace_dir() / "traittutor" / "capability_decisions.json"
        )

    def _lock_path(self) -> Path:
        return self._path().with_suffix(".lock")

    @staticmethod
    def _empty() -> dict[str, Any]:
        return {"schema_version": _SCHEMA_VERSION, "decisions": [], "idempotency": []}

    def _load(self) -> dict[str, Any]:
        try:
            payload = self._adapter.snapshot()
        except Exception as exc:
            raise CapabilityDecisionStoreError("unable to read capability decisions") from exc
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != _SCHEMA_VERSION
            or not isinstance(payload.get("decisions"), list)
            or not isinstance(payload.get("idempotency"), list)
        ):
            raise CapabilityDecisionStoreError("capability decision data has an invalid format")
        return payload

    @contextmanager
    def _locked(self) -> Iterator[dict[str, Any]]:
        with self._adapter.locked() as payload:
            yield payload

    def _owned(self, payload: dict[str, Any]) -> list[CapabilityDecision]:
        try:
            return [
                CapabilityDecision.model_validate(item)
                for item in payload["decisions"]
                if isinstance(item, dict) and item.get("owner_id") == self.owner_id
            ]
        except ValidationError as exc:
            raise CapabilityDecisionStoreError("capability decision data is invalid") from exc

    def get(self, decision_id: str) -> CapabilityDecision:
        return (
            next(
                (item for item in self._owned(self._load()) if item.decision_id == decision_id),
                None,
            )
            or self._missing()
        )

    @staticmethod
    def _missing() -> CapabilityDecision:
        raise CapabilityDecisionNotFound("Capability decision not found")

    def create_or_replay(
        self,
        decision: CapabilityDecision,
        *,
        idempotency_key: str,
        request_hash: str,
    ) -> tuple[CapabilityDecision, bool]:
        """Write one decision, or return its exact owner-local replay."""
        if decision.owner_id != self.owner_id:
            raise CapabilityDecisionStoreError("decision owner does not match store owner")
        key_hash = _key_hash(idempotency_key)
        with self._locked() as payload:
            replay_record = next(
                (
                    item
                    for item in payload["idempotency"]
                    if isinstance(item, dict)
                    and item.get("owner_id") == self.owner_id
                    and item.get("kind") == "route"
                    and item.get("key_hash") == key_hash
                ),
                None,
            )
            if replay_record is not None:
                if replay_record.get("request_hash") != request_hash:
                    raise CapabilityDecisionIdempotencyConflict(
                        "idempotency key reused with another route request"
                    )
                replay_id = replay_record.get("decision_id")
                replay = next(
                    (item for item in self._owned(payload) if item.decision_id == replay_id), None
                )
                if replay is None:
                    raise CapabilityDecisionStoreError(
                        "idempotency record references a missing decision"
                    )
                return replay, True
            payload["decisions"].append(decision.model_dump(mode="json"))
            payload["idempotency"].append(
                {
                    "owner_id": self.owner_id,
                    "kind": "route",
                    "key_hash": key_hash,
                    "request_hash": request_hash,
                    "decision_id": decision.decision_id,
                }
            )
            self._adapter.replace_all(payload)
            return decision, False

    def replace(self, decision: CapabilityDecision) -> CapabilityDecision:
        """CAS-free state advance used only by the route service under its run lock.

        The decision id and owner are immutable.  A stale or cross-owner call
        is indistinguishable from a missing id.
        """
        if decision.owner_id != self.owner_id:
            raise CapabilityDecisionNotFound("Capability decision not found")
        with self._locked() as payload:
            for index, record in enumerate(payload["decisions"]):
                if not isinstance(record, dict):
                    continue
                if (
                    record.get("owner_id") == self.owner_id
                    and record.get("decision_id") == decision.decision_id
                ):
                    previous = CapabilityDecision.model_validate(record)
                    if decision.revision != previous.revision + 1:
                        raise CapabilityDecisionStoreError("stale decision revision")
                    payload["decisions"][index] = decision.model_dump(mode="json")
                    self._adapter.replace_all(payload)
                    return decision
            raise CapabilityDecisionNotFound("Capability decision not found")

    def confirm_or_replay(
        self,
        decision_id: str,
        *,
        idempotency_key: str,
        request_hash: str,
        confirmed: CapabilityDecision,
    ) -> tuple[CapabilityDecision, bool]:
        """Advance a confirmation once and record replay identity atomically."""
        key_hash = _key_hash(idempotency_key)
        with self._locked() as payload:
            replay_record = next(
                (
                    item
                    for item in payload["idempotency"]
                    if isinstance(item, dict)
                    and item.get("owner_id") == self.owner_id
                    and item.get("kind") == "confirm"
                    and item.get("decision_id") == decision_id
                    and item.get("key_hash") == key_hash
                ),
                None,
            )
            current_index = next(
                (
                    index
                    for index, record in enumerate(payload["decisions"])
                    if isinstance(record, dict)
                    and record.get("owner_id") == self.owner_id
                    and record.get("decision_id") == decision_id
                ),
                None,
            )
            if current_index is None:
                raise CapabilityDecisionNotFound("Capability decision not found")
            current = CapabilityDecision.model_validate(payload["decisions"][current_index])
            if replay_record is not None:
                if replay_record.get("request_hash") != request_hash:
                    raise CapabilityDecisionIdempotencyConflict(
                        "idempotency key reused with another confirmation"
                    )
                return current, True
            if current.status in {"confirmed", "completed"}:
                # A new key after successful confirmation (or after the
                # research owner recorded its completed hand-off) is also a
                # replay, never a second expensive action hand-off.
                return current, True
            if current.status != "confirmation_required":
                raise CapabilityDecisionStoreError("decision does not require confirmation")
            if confirmed.owner_id != self.owner_id or confirmed.decision_id != decision_id:
                raise CapabilityDecisionStoreError("confirmation does not match decision")
            if confirmed.revision != current.revision + 1:
                raise CapabilityDecisionStoreError("stale decision revision")
            payload["decisions"][current_index] = confirmed.model_dump(mode="json")
            payload["idempotency"].append(
                {
                    "owner_id": self.owner_id,
                    "kind": "confirm",
                    "key_hash": key_hash,
                    "request_hash": request_hash,
                    "decision_id": decision_id,
                }
            )
            self._adapter.replace_all(payload)
            return confirmed, False

    def complete_research_action(
        self,
        decision_id: str,
        *,
        action_target: dict[str, object],
    ) -> tuple[CapabilityDecision, bool]:
        """Persist one research hand-off after its durable run exists.

        The research store owns creation idempotency.  This second, owner-local
        receipt owns scheduling idempotency: only the request which advances a
        confirmed decision to ``completed`` may enqueue the run.  Replays can
        therefore safely reconstruct the same result without a second worker
        hand-off.
        """
        return self._complete_action(
            decision_id,
            expected_capability="research",
            action_target=action_target,
        )

    def complete_learn_action(
        self,
        decision_id: str,
        *,
        action_target: dict[str, object],
    ) -> tuple[CapabilityDecision, bool]:
        """Persist one confirmed Pack/Plan hand-off after durable creation."""
        return self._complete_action(
            decision_id,
            expected_capability="learn",
            action_target=action_target,
        )

    def complete_create_action(
        self,
        decision_id: str,
        *,
        action_target: dict[str, object],
    ) -> tuple[CapabilityDecision, bool]:
        """Persist one confirmed, owner-bound courseware task hand-off."""
        return self._complete_action(
            decision_id,
            expected_capability="create",
            action_target=action_target,
        )

    def _complete_action(
        self,
        decision_id: str,
        *,
        expected_capability: str,
        action_target: dict[str, object],
    ) -> tuple[CapabilityDecision, bool]:
        """Advance one confirmed decision exactly once under the owner lock."""
        with self._locked() as payload:
            current_index = next(
                (
                    index
                    for index, record in enumerate(payload["decisions"])
                    if isinstance(record, dict)
                    and record.get("owner_id") == self.owner_id
                    and record.get("decision_id") == decision_id
                ),
                None,
            )
            if current_index is None:
                raise CapabilityDecisionNotFound("Capability decision not found")
            current = CapabilityDecision.model_validate(payload["decisions"][current_index])
            if current.capability != expected_capability:
                raise CapabilityDecisionStoreError(
                    f"decision is not a {expected_capability} hand-off"
                )
            if current.status == "completed":
                return current, True
            if current.status != "confirmed":
                raise CapabilityDecisionStoreError(
                    f"{expected_capability} decision is not confirmed"
                )
            completed = current.model_copy(
                update={
                    "status": "completed",
                    "action_target": action_target,
                    "reason": _COMPLETION_REASONS[expected_capability],
                    "revision": current.revision + 1,
                    "updated_at": current.updated_at,
                }
            )
            # Keep the completion timestamp separate from consent only when
            # the hand-off is durably recorded; import lazily to avoid making
            # the store depend on service orchestration.
            from .models import utc_now

            completed = completed.model_copy(update={"updated_at": utc_now()})
            payload["decisions"][current_index] = completed.model_dump(mode="json")
            self._adapter.replace_all(payload)
            return completed, False


__all__ = [
    "CapabilityDecisionIdempotencyConflict",
    "CapabilityDecisionNotFound",
    "CapabilityDecisionStore",
    "CapabilityDecisionStoreError",
]
