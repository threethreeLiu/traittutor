"""Deterministic, delivery-free authorization for proactive Tutor reminders.

The Persona profile stores a preference and explicit consent; this module turns
them into a server-side eligibility decision. It intentionally does not queue,
compose, or transmit a notification. A later delivery worker must consume this
decision rather than treating Persona wording as permission to contact a user.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict

from .models import TutorPersonaProfile

ReminderEligibilityReason = Literal[
    "eligible",
    "proactivity_off",
    "consent_required",
    "quiet_hours",
]


class ReminderEligibility(BaseModel):
    """A public, payload-free decision for a future owner-bound scheduler."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed: bool
    reason: ReminderEligibilityReason
    evaluated_at: str


def reminder_eligibility(
    profile: TutorPersonaProfile,
    *,
    now: datetime | None = None,
) -> ReminderEligibility:
    """Fail closed unless mode, consent, and local quiet-hours all permit contact."""
    evaluated = _utc_now(now)
    if profile.proactivity == "off":
        return _decision(False, "proactivity_off", evaluated)
    if not profile.reminder_consent:
        return _decision(False, "consent_required", evaluated)
    if _within_quiet_hours(profile, evaluated):
        return _decision(False, "quiet_hours", evaluated)
    return _decision(True, "eligible", evaluated)


def _utc_now(value: datetime | None) -> datetime:
    now = value or datetime.now(UTC)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("reminder evaluation time must be timezone-aware")
    return now.astimezone(UTC)


def _within_quiet_hours(profile: TutorPersonaProfile, now: datetime) -> bool:
    quiet = profile.quiet_hours
    if not quiet.enabled:
        return False
    local = now.astimezone(ZoneInfo(quiet.timezone))
    minute = local.hour * 60 + local.minute
    start = _minute_of_day(quiet.start_local)
    end = _minute_of_day(quiet.end_local)
    # Equal endpoints are deliberately treated as a full-day quiet window:
    # the ambiguous setting must never turn into an unsolicited reminder.
    if start == end:
        return True
    if start < end:
        return start <= minute < end
    return minute >= start or minute < end


def _minute_of_day(value: str) -> int:
    hour, minute = value.split(":", maxsplit=1)
    return int(hour) * 60 + int(minute)


def _decision(
    allowed: bool,
    reason: ReminderEligibilityReason,
    evaluated_at: datetime,
) -> ReminderEligibility:
    return ReminderEligibility(
        allowed=allowed,
        reason=reason,
        evaluated_at=evaluated_at.isoformat(),
    )


__all__ = [
    "ReminderEligibility",
    "ReminderEligibilityReason",
    "reminder_eligibility",
]
