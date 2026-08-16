"""Consent and quiet-hour tests for delivery-free Persona reminder eligibility."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from traittutor.tutor_persona.compiler import compile_persona
from traittutor.tutor_persona.models import QuietHours, TutorPersonaProfile, TutorPersonaSettings
from traittutor.tutor_persona.reminders import reminder_eligibility


def _profile(**updates: object) -> TutorPersonaProfile:
    settings = TutorPersonaSettings(**updates)
    return TutorPersonaProfile(
        **settings.model_dump(mode="python"),
        persona_id="tp_reminders",
        owner_id="owner-a",
        version=1,
        created_at="2026-08-10T00:00:00+00:00",
        updated_at="2026-08-10T00:00:00+00:00",
    )


def test_reminders_require_non_off_mode_and_explicit_consent() -> None:
    now = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    assert reminder_eligibility(_profile(), now=now).reason == "proactivity_off"
    assert (
        reminder_eligibility(_profile(proactivity="reminders_only"), now=now).reason
        == "consent_required"
    )
    decision = reminder_eligibility(
        _profile(proactivity="reminders_only", reminder_consent=True), now=now
    )
    assert decision.allowed is True
    assert decision.reason == "eligible"


def test_quiet_hours_apply_in_profile_timezone_and_fail_closed_for_equal_endpoints() -> None:
    shanghai = QuietHours(
        enabled=True, start_local="22:00", end_local="08:00", timezone="Asia/Shanghai"
    )
    profile = _profile(
        proactivity="moderate",
        reminder_consent=True,
        quiet_hours=shanghai,
    )
    assert (
        reminder_eligibility(profile, now=datetime(2026, 8, 10, 15, 0, tzinfo=UTC)).reason
        == "quiet_hours"
    )
    assert (
        reminder_eligibility(profile, now=datetime(2026, 8, 10, 2, 0, tzinfo=UTC)).allowed is True
    )

    full_day = _profile(
        proactivity="moderate",
        reminder_consent=True,
        quiet_hours=QuietHours(
            enabled=True, start_local="09:00", end_local="09:00", timezone="UTC"
        ),
    )
    assert (
        reminder_eligibility(full_day, now=datetime(2026, 8, 10, 12, 0, tzinfo=UTC)).reason
        == "quiet_hours"
    )


def test_reminder_evaluation_rejects_naive_time() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        reminder_eligibility(
            _profile(proactivity="moderate", reminder_consent=True),
            now=datetime(2026, 8, 10, 12, 0),
        )


def test_reminder_consent_never_changes_prompt_facing_persona_contract() -> None:
    baseline = _profile(proactivity="moderate", reminder_consent=False)
    consented = _profile(proactivity="moderate", reminder_consent=True)

    assert compile_persona(baseline) == compile_persona(consented)
