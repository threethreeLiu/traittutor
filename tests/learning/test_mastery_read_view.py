from __future__ import annotations

from traittutor.learning.models import (
    ErrorType,
    KnowledgePoint,
    KnowledgeType,
    LearningModule,
    LearningProgress,
    QuizAttempt,
)
from traittutor.learning.policy import (
    display_mastery,
    is_mastered,
    map_summary,
    next_objective,
)
from traittutor.learning.service import LearningService
from traittutor.learning_model import (
    KnowledgeStateStore,
    KnowledgeStateUnit,
    LearnerEvent,
    LearnerEventLedger,
)
from traittutor.learning_model.mastery_read_view import MasteryReadView

NOW = "2026-08-09T10:00:00+00:00"


def _progress() -> tuple[LearningProgress, KnowledgePoint]:
    kp = KnowledgePoint(
        id="kc1",
        name="Linear equations",
        type=KnowledgeType.PROCEDURE,
        module_id="m1",
    )
    return (
        LearningProgress(
            book_id="book1",
            modules=[
                LearningModule(
                    id="m1",
                    name="Algebra",
                    order=1,
                    knowledge_points=[kp],
                )
            ],
        ),
        kp,
    )


def _view(
    store: KnowledgeStateStore,
    *,
    user_id: str = "u1",
    subject_id: str = "math",
) -> MasteryReadView:
    return MasteryReadView.from_state_store(
        store,
        user_id=user_id,
        subject_id=subject_id,
    )


def test_missing_or_uncalibrated_is_unknown_not_zero(monkeypatch) -> None:
    progress, kp = _progress()

    missing_view = _view(KnowledgeStateStore())
    assert display_mastery(progress, kp, mastery_read_view=missing_view) is None
    missing = map_summary(progress, mastery_read_view=missing_view)["modules"][0][
        "knowledge_points"
    ][0]
    assert "mastery" not in missing
    assert missing["evidence_state"] == "insufficient_evidence"
    assert missing["verified_observation_count"] == 0
    assert "mastery_interval" not in missing

    store = KnowledgeStateStore()
    store.upsert(
        KnowledgeStateUnit(
            user_id="u1",
            subject_id="math",
            kc_id="kc1",
            mastery_probability=0.91,
            verified_observation_count=7,
            calibrated=False,
            param_version="v1-uncalibrated",
            updated_at=NOW,
        )
    )
    uncalibrated = map_summary(progress, mastery_read_view=_view(store))["modules"][0][
        "knowledge_points"
    ][0]
    assert "mastery" not in uncalibrated
    assert uncalibrated["evidence_state"] == "insufficient_evidence"
    assert uncalibrated["verified_observation_count"] == 7
    assert "mastery_interval" not in uncalibrated


def test_without_an_identity_bound_view_never_falls_back_to_retired_state(
    monkeypatch,
) -> None:
    """A path without a trusted subject mapping must fail closed, not show 0%."""
    progress, kp = _progress()
    assert display_mastery(progress, kp) is None
    assert is_mastered(progress, kp) is False
    assert next_objective(progress).action == "probe"
    item = map_summary(progress)["modules"][0]["knowledge_points"][0]
    assert "mastery" not in item
    assert item["status"] == "new"


def test_view_isolates_same_kc_by_user_and_subject(monkeypatch) -> None:
    progress, kp = _progress()
    store = KnowledgeStateStore()
    for user_id, subject_id, probability in (
        ("u1", "math", 0.82),
        ("u2", "math", 0.36),
        ("u1", "physics", 0.64),
    ):
        store.upsert(
            KnowledgeStateUnit(
                user_id=user_id,
                subject_id=subject_id,
                kc_id="kc1",
                mastery_probability=probability,
                verified_observation_count=10,
                calibrated=True,
                param_version="v2-calibrated",
                updated_at=NOW,
            )
        )

    assert display_mastery(progress, kp, mastery_read_view=_view(store)) == 0.82
    assert (
        display_mastery(
            progress,
            kp,
            mastery_read_view=_view(store, user_id="u2"),
        )
        == 0.36
    )
    assert (
        display_mastery(
            progress,
            kp,
            mastery_read_view=_view(store, subject_id="physics"),
        )
        == 0.64
    )
    assert (
        display_mastery(
            progress,
            kp,
            mastery_read_view=_view(store, user_id="u3"),
        )
        is None
    )


def test_same_canonical_state_drives_display_and_decision(monkeypatch) -> None:
    progress, kp = _progress()
    # Only the canonical state can unlock the objective.
    store = KnowledgeStateStore()
    store.upsert(
        KnowledgeStateUnit(
            user_id="u1",
            subject_id="math",
            kc_id="kc1",
            mastery_probability=0.4,
            verified_observation_count=8,
            calibrated=True,
            param_version="v2-calibrated",
            updated_at=NOW,
        )
    )
    view = _view(store)

    assert display_mastery(progress, kp, mastery_read_view=view) == 0.4
    assert is_mastered(progress, kp, mastery_read_view=view) is False
    assert next_objective(progress, mastery_read_view=view).action == "practice"
    assert map_summary(progress, mastery_read_view=view)["counts"]["mastered"] == 0

    store.upsert(
        KnowledgeStateUnit(
            user_id="u1",
            subject_id="math",
            kc_id="kc1",
            mastery_probability=0.95,
            verified_observation_count=9,
            calibrated=True,
            param_version="v2-calibrated",
            updated_at=NOW,
        )
    )

    assert is_mastered(progress, kp, mastery_read_view=view) is True
    assert next_objective(progress, mastery_read_view=view).action == "complete"
    assert map_summary(progress, mastery_read_view=view)["counts"]["mastered"] == 1


def test_ledger_adapter_reads_facts_without_writing_legacy_map(monkeypatch) -> None:
    progress, kp = _progress()
    before = progress.model_dump(mode="json")
    ledger = LearnerEventLedger()
    ledger.append(
        LearnerEvent(
            event_id="e1",
            idempotency_key="answer:e1",
            user_id="u1",
            subject_id="math",
            kc_ids=("kc1",),
            surface_type="practice",
            answer_correct=True,
            evidence_strength="strong",
            attribution_status="reliable",
            created_at=NOW,
        )
    )
    view = MasteryReadView.from_ledger(
        ledger,
        user_id="u1",
        subject_id="math",
    )

    item = map_summary(progress, mastery_read_view=view)["modules"][0]["knowledge_points"][0]
    assert display_mastery(progress, kp, mastery_read_view=view) is None
    assert "mastery" not in item
    assert item["verified_observation_count"] == 1
    assert item["model_version"] == "v1-uncalibrated"
    assert progress.model_dump(mode="json") == before
    assert "mastery_levels" not in before


def _two_kc_progress() -> LearningProgress:
    return LearningProgress(
        book_id="book-recovery",
        modules=[
            LearningModule(
                id="m1",
                name="Foundations",
                order=1,
                knowledge_points=[
                    KnowledgePoint(
                        id="kc1",
                        name="First concept",
                        type=KnowledgeType.CONCEPT,
                        module_id="m1",
                    ),
                    KnowledgePoint(
                        id="kc2",
                        name="Second concept",
                        type=KnowledgeType.CONCEPT,
                        module_id="m1",
                    ),
                ],
            )
        ],
    )


def test_two_failures_defer_kc_and_different_kc_attempt_releases_it() -> None:
    progress = _two_kc_progress()
    service = LearningService.__new__(LearningService)

    assert service.record_recovery_outcome(progress, "kc1", succeeded=False) is False
    assert next_objective(progress).knowledge_point_id == "kc1"

    assert service.record_recovery_outcome(progress, "kc1", succeeded=False) is True
    switched = next_objective(progress)
    assert switched.knowledge_point_id == "kc2"
    assert switched.action == "probe"
    assert progress.deferred_knowledge_points == {"kc1": 2}

    # Pass/fail is irrelevant to releasing the old KC: one trusted attempt on
    # a different objective is enough, and no mastery evidence is rewritten.
    service.record_recovery_outcome(progress, "kc2", succeeded=False)
    assert "kc1" not in progress.deferred_knowledge_points
    assert next_objective(progress).knowledge_point_id == "kc1"
    assert progress.qualitative_mastery == {}


def test_single_kc_recovery_pause_requires_explicit_resume() -> None:
    progress, _kp = _progress()
    service = LearningService.__new__(LearningService)
    service.record_recovery_outcome(progress, "kc1", succeeded=False)
    service.record_recovery_outcome(progress, "kc1", succeeded=False)

    paused = next_objective(progress)
    assert paused.action == "recovery_pause"
    assert paused.deferred is True
    assert paused.knowledge_point_id == "kc1"

    class _Store:
        def save(self, _progress: LearningProgress) -> None:
            return None

    service._store = _Store()
    assert service.resume_deferred_objective(progress, "kc1") is True
    assert next_objective(progress).action == "probe"
    assert progress.verified_observation_counts == {}


def test_correct_outcome_clears_consecutive_failure_count() -> None:
    progress = _two_kc_progress()
    service = LearningService.__new__(LearningService)
    service.record_recovery_outcome(progress, "kc1", succeeded=False)
    service.record_recovery_outcome(progress, "kc1", succeeded=True)
    service.record_recovery_outcome(progress, "kc1", succeeded=False)

    assert progress.consecutive_failures_by_kc["kc1"] == 1
    assert "kc1" not in progress.deferred_knowledge_points


def test_derived_attempt_and_error_details_are_bounded_with_lifetime_totals() -> None:
    progress, _kp = _progress()
    service = LearningService.__new__(LearningService)
    for index in range(300):
        assert service.record_quiz_attempt(
            progress,
            QuizAttempt(
                question_id="q1",
                knowledge_point_id="kc1",
                module_id="m1",
                is_correct=False,
                error_type=ErrorType.UNDERSTANDING_DEVIATION,
                event_id=f"event-{index}",
            ),
        )

    assert len(progress.quiz_attempts) == 256
    error = progress.error_records[0]
    assert len(error.retry_history) == 32
    assert error.total_retry_count == 299
    assert [item.attempt_number for item in error.retry_history] == list(range(268, 300))
    assert len(error.source_event_ids) == 32
    assert error.total_source_event_count == 300
