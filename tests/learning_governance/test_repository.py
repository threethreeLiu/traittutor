from __future__ import annotations

from traittutor.learning.models import (
    ErrorRecord,
    ErrorRecordStatus,
    ErrorType,
    KnowledgeType,
    LearningProgress,
    RepetitionState,
    RetryAttempt,
    ReviewTask,
)
from traittutor.learning.storage import LearningStore
from traittutor.learning_governance.models import GovernanceAttributionStatus
from traittutor.learning_governance.repository import (
    LearningGovernanceRepository,
    OwnerBoundLearningStore,
)
from traittutor.learning_model.events import LearnerEvent, LearnerEventLedger
from traittutor.learning_model.misconception import MisconceptionStore

NOW = "2026-08-10T00:00:00Z"


def _event(
    event_id: str,
    *,
    user_id: str = "user-a",
    subject_id: str = "math",
    kc_id: str = "fractions",
    strong: bool = True,
) -> LearnerEvent:
    return LearnerEvent(
        event_id=event_id,
        idempotency_key=f"idem-{event_id}",
        user_id=user_id,
        subject_id=subject_id,
        kc_ids=(kc_id,),
        surface_type="quiz",
        item_id=f"question-{event_id}",
        answer_correct=False,
        evidence_strength="strong" if strong else "exposure",
        attribution_status="reliable" if strong else "attribution_pending",
        created_at=NOW,
    )


def _error(error_id: str, event_id: str, *, kc_id: str = "fractions") -> ErrorRecord:
    return ErrorRecord(
        id=error_id,
        question_id=f"question-{error_id}",
        knowledge_point_id=kc_id,
        module_id="module-1",
        error_type=ErrorType.APPLICATION_ERROR,
        status=ErrorRecordStatus.OPEN,
        source_event_ids=[event_id],
        retry_history=[
            RetryAttempt(
                timestamp=2.0,
                is_correct=True,
                attempt_number=1,
                event_id=f"repair-{event_id}",
            )
        ],
        created_at=1.0,
    )


def _save_progress(
    store: LearningStore,
    *,
    book_id: str,
    subject_id: str,
    errors: list[ErrorRecord],
    kc_id: str = "fractions",
) -> None:
    state = RepetitionState(next_review_at=5.0)
    store.save(
        LearningProgress(
            book_id=book_id,
            subject_id=subject_id,
            error_records=errors,
            review_queue=[
                ReviewTask(
                    id=f"review-{kc_id}",
                    knowledge_point_id=kc_id,
                    knowledge_type=KnowledgeType.CONCEPT,
                    due_at=state.next_review_at,
                    priority=1,
                    state=state,
                )
            ],
        )
    )


def _repository(tmp_path) -> LearningGovernanceRepository:
    learning_store = LearningStore(tmp_path / "user-a-learning")
    _save_progress(
        learning_store,
        book_id="math-book",
        subject_id="math",
        errors=[_error("verified", "event-verified")],
    )
    _save_progress(
        learning_store,
        book_id="physics-book",
        subject_id="physics",
        errors=[_error("physics", "event-physics", kc_id="vectors")],
        kc_id="vectors",
    )
    _save_progress(
        learning_store,
        book_id="legacy-book",
        subject_id="",
        errors=[
            _error("pending", "event-pending"),
            _error("foreign", "event-foreign"),
        ],
    )

    ledger = LearnerEventLedger()
    for event in (
        _event("event-verified"),
        _event("event-pending", strong=False),
        _event("event-foreign", user_id="user-b"),
        _event("event-physics", subject_id="physics", kc_id="vectors"),
    ):
        ledger.append(event)

    misconception_store = MisconceptionStore(
        tmp_path / "user-a-misconceptions.json",
        owner_id="user-a",
    )
    misconception_store.propose(
        hypothesis_id="confirmed-source",
        user_id="user-a",
        subject_id="math",
        kc_ids=("fractions",),
        rubric_ref="private-rubric",
        pattern="reverses numerator and denominator",
        evidence_refs=("event-verified",),
        created_at=NOW,
    )
    misconception_store.propose(
        hypothesis_id="pending-source",
        user_id="user-a",
        subject_id="math",
        kc_ids=("fractions",),
        rubric_ref="private-rubric",
        pattern="adds unlike denominators directly",
        evidence_refs=("missing-event",),
        created_at=NOW,
    )
    return LearningGovernanceRepository(
        owner_id="user-a",
        learning_source=OwnerBoundLearningStore(owner_id="user-a", store=learning_store),
        event_ledger=ledger,
        misconception_store=misconception_store,
    )


def test_errors_are_subject_kc_and_owner_isolated(tmp_path) -> None:
    repository = _repository(tmp_path)

    errors = repository.list_errors(subject_id="math", kc_id="fractions")

    by_id = {item.error_id: item for item in errors}
    assert set(by_id) == {"verified", "pending"}
    assert by_id["verified"].attribution_status == GovernanceAttributionStatus.VERIFIED
    assert by_id["pending"].attribution_status == GovernanceAttributionStatus.ATTRIBUTION_PENDING
    assert repository.list_errors(subject_id="math", kc_id="vectors") == []
    assert [item.error_id for item in repository.list_errors(subject_id="physics")] == ["physics"]


def test_repairs_and_reviews_are_safe_subject_scoped_projections(tmp_path) -> None:
    repository = _repository(tmp_path)

    repairs = repository.list_repairs(subject_id="math", kc_id="fractions")
    reviews = repository.list_reviews(subject_id="math", now=10.0)

    assert [item.error_id for item in repairs] == ["pending", "verified"]
    assert all(item.attempt_count == 1 for item in repairs)
    assert reviews == []
    assert repository.list_reviews(subject_id="physics", kc_id="fractions", now=10.0) == []


def test_misconceptions_expose_pattern_but_not_private_rubric(tmp_path) -> None:
    repository = _repository(tmp_path)

    items = repository.list_misconceptions(subject_id="math", kc_id="fractions")

    assert [item.hypothesis_id for item in items] == [
        "confirmed-source",
        "pending-source",
    ]
    assert items[0].attribution_status == GovernanceAttributionStatus.VERIFIED
    assert items[1].attribution_status == GovernanceAttributionStatus.ATTRIBUTION_PENDING
    assert all("rubric" not in item.model_dump(mode="json") for item in items)


def test_repository_rejects_mismatched_owner_bindings(tmp_path) -> None:
    learning_store = LearningStore(tmp_path / "learning")
    misconception_store = MisconceptionStore(
        tmp_path / "misconceptions.json",
        owner_id="user-a",
    )

    try:
        LearningGovernanceRepository(
            owner_id="user-a",
            learning_source=OwnerBoundLearningStore(owner_id="user-b", store=learning_store),
            event_ledger=LearnerEventLedger(),
            misconception_store=misconception_store,
        )
    except PermissionError as exc:
        assert "owner" in str(exc)
    else:
        raise AssertionError("mismatched learning owner must fail closed")
