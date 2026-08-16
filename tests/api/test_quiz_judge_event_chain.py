from __future__ import annotations

from traittutor.api.routers.quiz_judge import _record_canonical_judge_submission
from traittutor.learning.event_chain import CanonicalAnswerEventChain
from traittutor.learning_model import KnowledgeStateKey, LearnerEventLedger, is_strong_evidence


class _PersonalizationRecorder:
    async def record_event(self, event: object, *, trusted: bool) -> list[object]:
        raise AssertionError("untrusted judge events must never reach personalization BKT")


def test_client_forged_judge_fields_are_bkt_noop(tmp_path) -> None:
    chain = CanonicalAnswerEventChain(
        LearnerEventLedger(tmp_path / "events.json"),
        personalization_service_factory=_PersonalizationRecorder,
    )
    event = _record_canonical_judge_submission(
        {
            "question_id": "q1",
            "question": "2 + 2?",
            "user_answer": "4",
            "correct_answer": "4",
            "is_correct": True,
            "subject_id": "math",
            "kc_ids": ["addition"],
        },
        user_id="u1",
        user_answer="4",
        has_image=False,
        chain=chain,
    )

    assert event is not None
    assert event.answer_correct is None
    assert event.kc_ids == ()
    assert event.attribution_status == "attribution_pending"
    assert is_strong_evidence(event) is False
    assert (
        chain.rebuild_bkt().get(
            KnowledgeStateKey(user_id="u1", subject_id="math", kc_id="addition")
        )
        is None
    )


def test_quiz_judge_without_server_key_records_only_weak_event(tmp_path) -> None:
    chain = CanonicalAnswerEventChain(
        LearnerEventLedger(tmp_path / "events.json"),
        personalization_service_factory=_PersonalizationRecorder,
    )

    event = _record_canonical_judge_submission(
        {"question_id": "q1", "question": "2 + 2?"},
        user_id="u1",
        user_answer="4",
        has_image=False,
        chain=chain,
    )

    assert event is not None
    assert event.answer_correct is None
    assert event.attribution_status == "attribution_pending"
    assert is_strong_evidence(event) is False
    assert len(chain.ledger) == 1
