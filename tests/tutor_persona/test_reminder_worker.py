from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from traittutor.learning.models import KnowledgeType
from traittutor.learning_governance.models import (
    GovernanceAttributionStatus,
    ReviewStatus,
    ReviewSummary,
)
from traittutor.multi_user.models import CurrentUser, UserScope
from traittutor.multi_user.paths import user_context
from traittutor.tutor_persona import reminder_worker
from traittutor.tutor_persona.models import TutorPersonaSettings
from traittutor.tutor_persona.reminder_outbox import TutorReminderOutbox
from traittutor.tutor_persona.store import TutorPersonaStore


class _Repository:
    def subject_sources(self):
        return {"math": ("review-items",)}

    def list_reviews(self, *, subject_id: str, now: float):
        assert subject_id == "math" and now > 0
        return [
            ReviewSummary(
                review_id="path:review",
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
        ]


def test_worker_delivers_due_review_once_for_consented_owner(
    tmp_path: Path,
    monkeypatch,
) -> None:
    owner = CurrentUser(
        id="alice",
        username="alice",
        role="user",
        scope=UserScope(kind="user", user_id="alice", root=tmp_path / "alice"),
    )
    with user_context(owner):
        store = TutorPersonaStore("alice")
        current = store.get_or_create_default()
        store.update(
            TutorPersonaSettings(proactivity="reminders_only", reminder_consent=True),
            expected_version=current.version,
            idempotency_key="consent",
        )
    monkeypatch.setattr(
        reminder_worker, "build_governance_repository", lambda _owner: _Repository()
    )

    first = reminder_worker.dispatch_tutor_reminders_once(
        owners=(owner,),
        now=datetime(2026, 8, 11, 9, 0, tzinfo=UTC),
    )
    second = reminder_worker.dispatch_tutor_reminders_once(
        owners=(owner,),
        now=datetime(2026, 8, 11, 9, 1, tzinfo=UTC),
    )

    assert first[0].queued == first[0].delivered == 1
    assert second[0].queued == second[0].delivered == 0
    with user_context(owner):
        reminders = TutorReminderOutbox("alice").list()
    assert len(reminders) == 1 and reminders[0].status == "delivered"
