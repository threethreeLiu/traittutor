"""Owner-bound durable in-app delivery for consented learning reminders."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterator, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from traittutor.learning_governance.models import ReviewSummary
from traittutor.services.path_service import get_path_service
from traittutor.unified_storage import SectionedRecordStore

from .models import TutorPersonaProfile
from .reminders import reminder_eligibility

ReminderDeliveryStatus = Literal["queued", "delivered", "read", "cancelled"]


def _now() -> datetime:
    return datetime.now(UTC)


class TutorReminder(BaseModel):
    """A reference-only reminder; answers and learning evidence stay in their stores."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    reminder_id: str = Field(min_length=1, max_length=96)
    owner_id: str = Field(min_length=1, max_length=128)
    kind: Literal["review_due"] = "review_due"
    reference_id: str = Field(min_length=1, max_length=160)
    learning_path_id: str = Field(min_length=1, max_length=160)
    subject_id: str = Field(min_length=1, max_length=96)
    kc_id: str = Field(min_length=1, max_length=96)
    due_at: str
    profile_version: int = Field(ge=1)
    status: ReminderDeliveryStatus = "queued"
    attempts: int = Field(default=0, ge=0)
    queued_at: str
    delivered_at: str | None = None
    read_at: str | None = None
    cancelled_at: str | None = None


class TutorReminderOutbox:
    """Persist scheduling, delivery, acknowledgement, cancellation, and audit."""

    def __init__(self, owner_id: str, *, path: Path | None = None) -> None:
        if not owner_id.strip():
            raise ValueError("owner_id is required")
        self.owner_id = owner_id
        self.path = path or (
            get_path_service().get_workspace_dir() / "traittutor" / "persona-reminders.json"
        )
        self._adapter = SectionedRecordStore(
            "tutor_reminders",
            owner_id,
            schema_version=1,
            path_service=get_path_service() if path is None else None,
            legacy_path=path,
        )

    @staticmethod
    def _empty() -> dict[str, Any]:
        return {"schema_version": 1, "reminders": [], "audit": []}

    def _load(self) -> dict[str, Any]:
        try:
            payload = self._adapter.snapshot()
        except Exception as exc:
            raise RuntimeError("Tutor reminder outbox cannot be read safely") from exc
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != 1
            or not isinstance(payload.get("reminders"), list)
            or not isinstance(payload.get("audit"), list)
        ):
            raise RuntimeError("Tutor reminder outbox has an invalid format")
        return payload

    @contextmanager
    def _locked(self) -> Iterator[dict[str, Any]]:
        with self._adapter.locked() as payload:
            yield payload

    def _owned(self, payload: dict[str, Any]) -> list[TutorReminder]:
        try:
            return [
                TutorReminder.model_validate(row)
                for row in payload["reminders"]
                if isinstance(row, dict) and row.get("owner_id") == self.owner_id
            ]
        except ValidationError as exc:
            raise RuntimeError("Tutor reminder outbox contains invalid records") from exc

    def _save(self, payload: dict[str, Any]) -> None:
        self._adapter.replace_all(payload)

    @staticmethod
    def _replace(payload: dict[str, Any], reminder: TutorReminder) -> None:
        for index, row in enumerate(payload["reminders"]):
            if (
                isinstance(row, dict)
                and row.get("owner_id") == reminder.owner_id
                and row.get("reminder_id") == reminder.reminder_id
            ):
                payload["reminders"][index] = reminder.model_dump(mode="json")
                return
        payload["reminders"].append(reminder.model_dump(mode="json"))

    def _audit(
        self, payload: dict[str, Any], reminder: TutorReminder, action: str, at: str
    ) -> None:
        payload["audit"].append(
            {
                "owner_id": self.owner_id,
                "reminder_id": reminder.reminder_id,
                "action": action,
                "created_at": at,
            }
        )

    def schedule_review(
        self,
        review: ReviewSummary,
        *,
        profile_version: int,
        now: datetime | None = None,
    ) -> TutorReminder:
        queued_at = (now or _now()).astimezone(UTC).isoformat()
        due_at = datetime.fromtimestamp(review.due_at, UTC).isoformat()
        fingerprint = sha256(
            f"{self.owner_id}\x1f{review.review_id}\x1f{due_at}".encode("utf-8")
        ).hexdigest()[:32]
        reminder_id = f"rem_{fingerprint}"
        with self._locked() as payload:
            existing = next(
                (item for item in self._owned(payload) if item.reminder_id == reminder_id),
                None,
            )
            if existing is not None:
                return existing
            reminder = TutorReminder(
                reminder_id=reminder_id,
                owner_id=self.owner_id,
                reference_id=review.review_id,
                learning_path_id=review.learning_path_id,
                subject_id=review.subject_id,
                kc_id=review.kc_id,
                due_at=due_at,
                profile_version=profile_version,
                queued_at=queued_at,
            )
            self._replace(payload, reminder)
            self._audit(payload, reminder, "queued", queued_at)
            self._save(payload)
            return reminder

    def reconcile_and_deliver(
        self,
        *,
        profile: TutorPersonaProfile,
        active_reference_ids: set[str],
        now: datetime | None = None,
        limit: int = 20,
    ) -> tuple[TutorReminder, ...]:
        if limit < 1:
            raise ValueError("limit must be positive")
        evaluated = (now or _now()).astimezone(UTC)
        decision = reminder_eligibility(profile, now=evaluated)
        changed: list[TutorReminder] = []
        with self._locked() as payload:
            for current in self._owned(payload):
                if current.status != "queued":
                    continue
                if current.reference_id not in active_reference_ids or decision.reason in {
                    "proactivity_off",
                    "consent_required",
                }:
                    updated = current.model_copy(
                        update={"status": "cancelled", "cancelled_at": evaluated.isoformat()}
                    )
                    self._replace(payload, updated)
                    self._audit(payload, updated, "cancelled", evaluated.isoformat())
                    changed.append(updated)
            if decision.allowed:
                queued = [
                    item
                    for item in self._owned(payload)
                    if item.status == "queued" and item.reference_id in active_reference_ids
                ][:limit]
                for current in queued:
                    delivered = current.model_copy(
                        update={
                            "status": "delivered",
                            "attempts": current.attempts + 1,
                            "delivered_at": evaluated.isoformat(),
                        }
                    )
                    self._replace(payload, delivered)
                    self._audit(payload, delivered, "delivered", evaluated.isoformat())
                    changed.append(delivered)
            if changed:
                self._save(payload)
        return tuple(changed)

    def list(self, *, status: ReminderDeliveryStatus | None = None) -> tuple[TutorReminder, ...]:
        items = self._owned(self._load())
        if status is not None:
            items = [item for item in items if item.status == status]
        return tuple(
            sorted(items, key=lambda item: (item.queued_at, item.reminder_id), reverse=True)
        )

    def acknowledge(self, reminder_id: str, *, now: datetime | None = None) -> TutorReminder:
        observed = (now or _now()).astimezone(UTC).isoformat()
        with self._locked() as payload:
            current = next(
                (item for item in self._owned(payload) if item.reminder_id == reminder_id),
                None,
            )
            if current is None:
                raise KeyError(reminder_id)
            if current.status == "read":
                return current
            if current.status != "delivered":
                raise ValueError("only delivered reminders can be acknowledged")
            updated = current.model_copy(update={"status": "read", "read_at": observed})
            self._replace(payload, updated)
            self._audit(payload, updated, "read", observed)
            self._save(payload)
            return updated

    def cancel(self, reminder_id: str, *, now: datetime | None = None) -> TutorReminder:
        observed = (now or _now()).astimezone(UTC).isoformat()
        with self._locked() as payload:
            current = next(
                (item for item in self._owned(payload) if item.reminder_id == reminder_id),
                None,
            )
            if current is None:
                raise KeyError(reminder_id)
            if current.status in {"cancelled", "read"}:
                return current
            updated = current.model_copy(update={"status": "cancelled", "cancelled_at": observed})
            self._replace(payload, updated)
            self._audit(payload, updated, "cancelled", observed)
            self._save(payload)
            return updated


__all__ = ["ReminderDeliveryStatus", "TutorReminder", "TutorReminderOutbox"]
