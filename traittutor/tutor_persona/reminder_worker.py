"""Cross-owner scheduler for due canonical review reminders."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Iterable

from traittutor.learning_governance.models import ReviewStatus
from traittutor.learning_governance.runtime import build_governance_repository
from traittutor.multi_user.models import CurrentUser
from traittutor.multi_user.paths import user_context
from traittutor.operations import active_owner_contexts

from .reminder_outbox import TutorReminderOutbox
from .store import TutorPersonaStore


@dataclass(frozen=True, slots=True)
class ReminderDispatchResult:
    owner_id: str
    queued: int
    delivered: int
    failed: bool = False


def dispatch_tutor_reminders_once(
    *,
    owners: Iterable[CurrentUser] | None = None,
    now: datetime | None = None,
) -> tuple[ReminderDispatchResult, ...]:
    """Discover due reviews, deduplicate them, and deliver to the in-app inbox."""

    observed = (now or datetime.now(UTC)).astimezone(UTC)
    results: list[ReminderDispatchResult] = []
    for owner in owners or active_owner_contexts():
        try:
            with user_context(owner):
                profile = TutorPersonaStore(owner.id).get_or_create_default()
                repository = build_governance_repository(owner)
                due = [
                    review
                    for subject_id in repository.subject_sources()
                    for review in repository.list_reviews(
                        subject_id=subject_id,
                        now=observed.timestamp(),
                    )
                    if review.status is ReviewStatus.DUE
                ]
                outbox = TutorReminderOutbox(owner.id)
                before = {item.reminder_id for item in outbox.list()}
                for review in due:
                    outbox.schedule_review(review, profile_version=profile.version, now=observed)
                after = {item.reminder_id for item in outbox.list()}
                changed = outbox.reconcile_and_deliver(
                    profile=profile,
                    active_reference_ids={review.review_id for review in due},
                    now=observed,
                )
            results.append(
                ReminderDispatchResult(
                    owner_id=owner.id,
                    queued=len(after - before),
                    delivered=sum(item.status == "delivered" for item in changed),
                )
            )
        except Exception:
            results.append(
                ReminderDispatchResult(owner_id=owner.id, queued=0, delivered=0, failed=True)
            )
    return tuple(results)


__all__ = ["ReminderDispatchResult", "dispatch_tutor_reminders_once"]
