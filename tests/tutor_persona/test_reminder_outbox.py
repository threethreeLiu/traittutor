from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from traittutor.learning.models import KnowledgeType
from traittutor.learning_governance.models import (
    GovernanceAttributionStatus,
    ReviewStatus,
    ReviewSummary,
)
from traittutor.tutor_persona.models import TutorPersonaProfile, TutorPersonaSettings
from traittutor.tutor_persona.reminder_outbox import TutorReminderOutbox


def _review(reference: str = "path:review") -> ReviewSummary:
    return ReviewSummary(
        review_id=reference,
        learning_path_id="path",
        subject_id="math",
        kc_id="fractions",
        knowledge_type=KnowledgeType.CONCEPT,
        due_at=1.0,
        priority=1,
        status=ReviewStatus.DUE,
        attribution_status=GovernanceAttributionStatus.VERIFIED,
        interval_index=1,
    )


def _profile(*, consent: bool = True, proactivity: str = "reminders_only") -> TutorPersonaProfile:
    settings = TutorPersonaSettings(
        proactivity=proactivity,  # type: ignore[arg-type]
        reminder_consent=consent,
    )
    return TutorPersonaProfile(
        **settings.model_dump(mode="python"),
        persona_id="persona",
        owner_id="alice",
        version=2,
        created_at="2026-08-11T00:00:00+00:00",
        updated_at="2026-08-11T00:00:00+00:00",
    )


def test_outbox_deduplicates_delivers_acknowledges_and_audits(tmp_path: Path) -> None:
    outbox = TutorReminderOutbox("alice", path=tmp_path / "outbox.json")
    now = datetime(2026, 8, 11, 9, 0, tzinfo=UTC)
    first = outbox.schedule_review(_review(), profile_version=2, now=now)
    replay = outbox.schedule_review(_review(), profile_version=2, now=now)

    assert replay == first
    delivered = outbox.reconcile_and_deliver(
        profile=_profile(),
        active_reference_ids={first.reference_id},
        now=now,
    )
    assert delivered[-1].status == "delivered"
    acknowledged = outbox.acknowledge(first.reminder_id, now=now)
    assert acknowledged.status == "read"
    assert outbox.acknowledge(first.reminder_id, now=now) == acknowledged
    actions = [record["action"] for record in outbox._adapter.snapshot()["audit"]]
    assert actions == ["queued", "delivered", "read"]


def test_consent_revocation_cancels_pending_and_quiet_hours_only_defers(tmp_path: Path) -> None:
    now = datetime(2026, 8, 11, 9, 0, tzinfo=UTC)
    quiet = TutorPersonaSettings(
        proactivity="reminders_only",
        reminder_consent=True,
        quiet_hours={
            "enabled": True,
            "start_local": "00:00",
            "end_local": "23:59",
            "timezone": "UTC",
        },
    )
    quiet_profile = TutorPersonaProfile(
        **quiet.model_dump(mode="python"),
        persona_id="persona",
        owner_id="alice",
        version=2,
        created_at=now.isoformat(),
        updated_at=now.isoformat(),
    )
    outbox = TutorReminderOutbox("alice", path=tmp_path / "outbox.json")
    reminder = outbox.schedule_review(_review(), profile_version=2, now=now)

    assert (
        outbox.reconcile_and_deliver(
            profile=quiet_profile,
            active_reference_ids={reminder.reference_id},
            now=now,
        )
        == ()
    )
    assert outbox.list()[0].status == "queued"
    changed = outbox.reconcile_and_deliver(
        profile=_profile(consent=False),
        active_reference_ids={reminder.reference_id},
        now=now,
    )
    assert changed[0].status == "cancelled"
