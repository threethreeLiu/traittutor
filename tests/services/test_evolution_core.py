from datetime import UTC, datetime, timedelta

import pytest

from traittutor.services.evolution import EvidenceRef, Hermes, Reflection, Trail, build_compass


def test_hermes_requires_confirmation_before_compass_uses_reflection():
    evidence = EvidenceRef("chat-1", "chat", "explicit request")
    trail = Trail("preference", {"statement": "用分步骤解释"}, (evidence,))
    reflection = Hermes().propose(trail)
    assert reflection is not None
    assert build_compass("quiz", [reflection]).reflection_ids == ()

    confirmed = reflection.__class__(
        statement=reflection.statement,
        category=reflection.category,
        evidence=reflection.evidence,
        confidence=reflection.confidence,
        subject_id=reflection.subject_id,
        state="confirmed",
        reflection_id=reflection.reflection_id,
    )
    compass = build_compass("quiz", [confirmed])
    assert compass.reflection_ids == (confirmed.reflection_id,)
    assert compass.evidence_ids == ("chat-1",)


def test_hermes_does_not_turn_quiz_result_into_free_form_preference():
    trail = Trail(
        "quiz_result",
        {"score": 1, "statement": "答错了一题"},
        (EvidenceRef("quiz-1", "quiz"),),
    )
    assert Hermes().propose(trail) is None


def test_compass_has_bounded_big_five_fallback_and_boundary():
    compass = build_compass("courseware", profile={"scores": {"C": 4, "O": 8}})
    context = compass.to_prompt_context()
    assert context["strategy"]["structure"] == "stepwise"
    assert "do not diagnose" in context["boundary"]


def test_expired_reflection_is_not_applied():
    reflection = Reflection(
        statement="旧偏好",
        category="preference",
        evidence=(EvidenceRef("chat-2", "chat"),),
        confidence=1,
        state="confirmed",
        expires_at=(datetime.now(UTC) - timedelta(days=1)).isoformat(),
    )
    assert build_compass("chat", [reflection]).reflection_ids == ()


def test_trail_and_compass_require_meaningful_scope():
    with pytest.raises(ValueError, match="evidence"):
        Trail("preference", {"statement": "x"}, ())
    with pytest.raises(ValueError, match="purpose"):
        build_compass("   ")
