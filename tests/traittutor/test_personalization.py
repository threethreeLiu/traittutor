from __future__ import annotations

from types import SimpleNamespace
from datetime import UTC, datetime, timedelta

import pytest

from traittutor.personalization.models import LearnerEvent, LearningSignal, SubjectRef, TeachingAction
from traittutor.personalization.service import PersonalizationService


@pytest.fixture
def learner_service(tmp_path, monkeypatch):
    from traittutor.personalization import service as module

    active_user = {"id": "learner-a"}
    monkeypatch.setattr(module.memory_paths, "memory_root", lambda: tmp_path / active_user["id"] / "memory")
    monkeypatch.setattr(module, "get_current_user", lambda: SimpleNamespace(id=active_user["id"]))
    return PersonalizationService(), active_user


def _subject() -> SubjectRef:
    return SubjectRef(subject_id="数学", label="数学", path=["数学", "函数"], confidence=0.9, source="material_analysis")


def _signal(signal_id: str, *, kind: str = "strategy_feedback", subject: SubjectRef | None = None) -> LearningSignal:
    return LearningSignal(
        signal_id=signal_id,
        kind=kind,
        subject_refs=[subject] if subject else [],
        payload={"strategy": TeachingAction(structure="worked_example").model_dump(), "task_type": "quiz", "positive": True},
        evidence_refs=[f"result:{signal_id}"], source="user", occurred_at="2026-07-29T00:00:00+00:00",
    )


def _subject_ref(subject_id: str, label: str, *path: str) -> SubjectRef:
    return SubjectRef(
        subject_id=subject_id,
        label=label,
        path=[label, *path],
        confidence=0.95,
        source="material_analysis",
    )


def _expected_bkt(
    prior: float,
    *,
    correct: bool,
    transition: float,
    guess: float,
    slip: float,
    weight: float,
) -> float:
    """Independent BKT oracle for strict regression tests.

    Keep this formula intentionally separate from ``traittutor.personalization``
    internals so the test catches accidental changes to event weighting,
    transition, guess, slip, or weighted posterior blending.
    """

    predicted = prior + (1 - prior) * transition
    likelihood = (
        predicted * (1 - slip) + (1 - predicted) * guess
        if correct
        else predicted * slip + (1 - predicted) * (1 - guess)
    )
    posterior = predicted if likelihood <= 0 else predicted * ((1 - slip) if correct else slip) / likelihood
    return max(0.0, min(1.0, prior * (1 - weight) + posterior * weight))


@pytest.mark.asyncio
async def test_explicit_preference_is_immediate_and_subject_ids_preserve_unicode(learner_service):
    service, _ = learner_service
    subject = _subject()
    assert service.classify_subject(material_analysis={"big_subject": "数学", "confidence": 0.8}).subject_id == "数学"
    signal = LearningSignal(signal_id="pref-1", kind="explicit_preference", payload={"value": "先举例", "category": "explanation"}, source="user", occurred_at="2026-07-29T00:00:00+00:00")
    await service.apply_signal(signal)
    context = service.build_context(purpose="chat", subject=subject)
    assert context.plan.rationale[0].source == "explicit_preference"
    assert "先举例" in context.plan.rationale[0].text


@pytest.mark.asyncio
async def test_candidate_reflections_do_not_enter_compass_until_confirmed(learner_service):
    service, _ = learner_service
    await service.apply_signal(LearningSignal(
        signal_id="candidate-pref", kind="strategy_feedback",
        payload={"value": "总是用类比开头", "category": "explanation"},
        evidence_refs=["chat:turn-2"], source="system",
        occurred_at="2026-07-29T00:00:00+00:00",
    ))

    reflection = service.reflections()[0]
    assert reflection.status == "candidate"
    assert reflection.applies_to_compass is False
    context = service.build_context(purpose="chat")
    assert "总是用类比开头" not in context.memory_snapshot.explicit_preferences
    assert all("总是用类比开头" not in item.text for item in context.plan.rationale)

    await service.decide_reflection("candidate-pref", "confirmed")
    confirmed = next(item for item in service.reflections() if item.reflection_id == "candidate-pref")
    assert confirmed.status == "confirmed"
    context = service.build_context(purpose="chat")
    assert "总是用类比开头" in context.memory_snapshot.explicit_preferences
    assert any("总是用类比开头" in item.text for item in context.plan.rationale)


@pytest.mark.asyncio
async def test_rejected_reflection_becomes_generation_constraint(learner_service):
    service, _ = learner_service
    await service.apply_signal(LearningSignal(
        signal_id="candidate-constraint", kind="strategy_feedback",
        payload={"value": "用很长的故事包装概念", "category": "explanation"},
        evidence_refs=["chat:turn-3"], source="system",
        occurred_at="2026-07-29T00:00:00+00:00",
    ))
    await service.decide_reflection("candidate-constraint", "rejected")

    reflection = next(item for item in service.reflections() if item.reflection_id == "candidate-constraint")
    assert reflection.status == "rejected"
    context = service.build_context(purpose="courseware")
    assert "用很长的故事包装概念" in context.constraints
    assert "用很长的故事包装概念" not in context.memory_snapshot.explicit_preferences


@pytest.mark.asyncio
async def test_reflection_decision_rebuilds_from_remaining_evidence(learner_service):
    service, _ = learner_service
    await service.apply_signal(LearningSignal(
        signal_id="candidate-rebuild", kind="strategy_feedback",
        payload={"value": "先用对比例子", "category": "explanation"},
        evidence_refs=["chat:turn-4"], source="system",
        occurred_at="2026-07-29T00:00:00+00:00",
    ))
    await service.decide_reflection("candidate-rebuild", "confirmed")
    assert next(item for item in service.reflections() if item.reflection_id == "candidate-rebuild").status == "confirmed"

    decision_id = next(item.signal_id for item in service.evidence() if item.kind == "reflection_decision")
    assert await service.delete_evidence(decision_id) is True

    rebuilt = next(item for item in service.reflections() if item.reflection_id == "candidate-rebuild")
    assert rebuilt.status == "candidate"
    context = service.build_context(purpose="chat")
    assert "先用对比例子" not in context.memory_snapshot.explicit_preferences


@pytest.mark.asyncio
async def test_expired_candidate_reflection_is_stale_and_not_in_compass(learner_service):
    service, _ = learner_service
    await service.apply_signal(LearningSignal(
        signal_id="expired-candidate", kind="strategy_feedback",
        payload={"value": "每段都加口诀", "category": "explanation"},
        evidence_refs=["chat:turn-5"], source="system",
        occurred_at="2026-07-29T00:00:00+00:00",
    ))
    profile = service.global_profile()
    expired = profile.preferences[0].model_copy(update={
        "expires_at": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
    })
    service._write_profile(service._global_path(), profile.model_copy(update={"preferences": [expired]}))

    reflection = next(item for item in service.reflections() if item.reflection_id == "expired-candidate")
    assert reflection.status == "stale"
    assert reflection.applies_to_compass is False
    context = service.build_context(purpose="chat")
    assert "每段都加口诀" not in context.memory_snapshot.explicit_preferences


@pytest.mark.asyncio
async def test_subject_scoped_reflection_decision_does_not_pollute_global_profile(learner_service):
    service, _ = learner_service
    subject = _subject()
    await service.apply_signal(LearningSignal(
        signal_id="subject-candidate", kind="strategy_feedback", subject_refs=[subject],
        payload={"value": "函数题先画图", "category": "explanation"},
        evidence_refs=["quiz:feedback"], source="system",
        occurred_at="2026-07-29T00:00:00+00:00",
    ))
    await service.decide_reflection("subject-candidate", "confirmed")

    assert service.global_profile().preferences == []
    subject_profile = service.subject_profile(subject.subject_id)
    assert subject_profile.preferences[0].state == "explicit"
    context = service.build_context(purpose="quiz", subject=subject)
    assert "函数题先画图" in context.memory_snapshot.explicit_preferences or any(
        "函数题先画图" in item.text for item in context.plan.rationale
    )


def test_subject_path_rejects_path_traversal(learner_service):
    service, _ = learner_service
    with pytest.raises(ValueError, match="invalid subject id"):
        service.subject_profile("../another-user")


@pytest.mark.asyncio
async def test_session_memory_snapshot_is_bounded_and_frozen_while_bkt_stays_live(learner_service):
    service, _ = learner_service
    subject = _subject()
    await service.apply_signal(LearningSignal(
        signal_id="goal", kind="explicit_preference", payload={"value": "掌握函数极限", "category": "goal"},
        evidence_refs=["memory:goal"], source="user", occurred_at="2026-07-29T00:00:00+00:00",
    ))
    first = service.build_context(purpose="quiz", subject=subject, session_id="session-a")
    assert first.memory_snapshot and first.memory_snapshot.goals == ["掌握函数极限"]
    await service.record_event(LearnerEvent(
        event_id="live-bkt", event_type="quiz_answer", subject=subject,
        concept_id="limits", concept_label="极限", observation="incorrect", confidence=.9,
        evidence_refs=["question:1"], occurred_at="2026-07-29T00:01:00+00:00",
    ), trusted=True)
    await service.apply_signal(LearningSignal(
        signal_id="new-pref", kind="explicit_preference", payload={"value": "先讲定义", "category": "explanation"},
        evidence_refs=["memory:pref"], source="user", occurred_at="2026-07-29T00:02:00+00:00",
    ))
    next_turn = service.build_context(purpose="quiz", subject=subject, session_id="session-a")
    assert next_turn.memory_snapshot and next_turn.memory_snapshot.snapshot_id == first.memory_snapshot.snapshot_id
    assert next_turn.relevant_concept_signals[0].concept_id == "limits"
    fresh_session = service.build_context(purpose="quiz", subject=subject, session_id="session-b")
    assert fresh_session.memory_snapshot and "先讲定义" in fresh_session.memory_snapshot.explicit_preferences


@pytest.mark.asyncio
async def test_inferred_strategy_requires_three_independent_events_and_respects_toggle(learner_service):
    service, _ = learner_service
    subject = _subject()
    for index in range(2):
        await service.apply_signal(_signal(f"feedback-{index}", subject=subject))
    assert service.build_context(purpose="quiz", subject=subject).plan.structure == "outline"
    await service.apply_signal(_signal("feedback-2", subject=subject))
    assert service.build_context(purpose="quiz", subject=subject).plan.structure == "worked_example"
    service.set_inference(False)
    await service.apply_signal(_signal("feedback-3", subject=subject))
    profile = service.subject_profile(subject.subject_id)
    assert len(profile.strategy_evidence[0].event_ids) == 3


@pytest.mark.asyncio
async def test_delete_evidence_rebuilds_and_user_roots_are_isolated(learner_service):
    service, active_user = learner_service
    await service.apply_signal(_signal("feedback-delete", subject=_subject()))
    assert await service.delete_evidence("feedback-delete") is True
    assert service.subjects() == []
    active_user["id"] = "learner-b"
    assert service.overview()["subjects"] == []


@pytest.mark.asyncio
async def test_knowledge_tracing_uses_graded_events_and_self_assessment_is_low_weight(learner_service):
    service, _ = learner_service
    subject = _subject()
    await service.record_event(LearnerEvent(
        event_id="self-known", event_type="self_assessment", subject=subject,
        concept_id="limits", concept_label="极限", observation="known", confidence=.35,
        occurred_at="2026-07-29T00:00:00+00:00",
    ))
    after_self = service.subject_profile(subject.subject_id).concept_signals[0]
    assert after_self.mastery_probability < .3
    assert after_self.verified_observation_count == 0
    graded_one = LearnerEvent(
        event_id="quiz-correct-1", event_type="quiz_answer", subject=subject,
        concept_id="limits", concept_label="极限", observation="correct", confidence=.9,
        evidence_refs=["question:1"], occurred_at="2026-07-29T00:01:00+00:00",
    )
    with pytest.raises(PermissionError):
        await service.record_event(graded_one)
    await service.record_event(graded_one, trusted=True)
    await service.record_event(LearnerEvent(
        event_id="quiz-correct-2", event_type="quiz_answer", subject=subject,
        concept_id="limits", concept_label="极限", observation="correct", confidence=.9,
        evidence_refs=["question:2"], occurred_at="2026-07-29T00:02:00+00:00",
    ), trusted=True)
    profile = service.subject_profile(subject.subject_id)
    concept = profile.concept_signals[0]
    assert concept.verified_observation_count == 2
    assert concept.mastery_probability > after_self.mastery_probability
    assert profile.understanding is not None
    assert profile.understanding.observed_concept_count == 1


@pytest.mark.asyncio
async def test_graph_reconciliation_rekeys_early_chunk_bkt_observations(learner_service):
    service, _ = learner_service
    subject = _subject()
    await service.record_event(LearnerEvent(
        event_id="chunk-observation", event_type="quiz_answer", subject=subject,
        concept_id="material.2", concept_label="牛顿第二定律", observation="incorrect",
        confidence=.9, evidence_refs=["question:2"], occurred_at="2026-07-29T00:00:00+00:00",
    ), trusted=True)

    service.reconcile_graph_concepts(subject, [{
        "concept_id": "physics.newtons-second-law", "label": "牛顿第二定律",
        "module_id": "mechanics", "confidence": .9, "evidence_chunk_ids": ["material.2"],
    }])
    signal = service.subject_profile(subject.subject_id).concept_signals[0]
    assert signal.concept_id == "physics.newtons-second-law"
    assert signal.module_id == "mechanics"
    assert signal.verified_observation_count == 1


@pytest.mark.asyncio
async def test_memory_reconcile_imports_referenced_l3_preferences_and_removes_stale_entries(learner_service):
    service, _ = learner_service
    from traittutor.services.memory.document import Document, Entry, serialize

    memory_path = service._root().parent / "L3" / "preferences.md"
    memory_path.parent.mkdir(parents=True)
    entry_id = "m_01ARZ3NDEKTSV4RRFFQ69G5FAV"
    memory_path.write_text(serialize(Document(title="Preferences", sections=[("Preferences", [Entry(entry_id, "Preferences", "先展示例题", ["chat:turn-1"])])])), encoding="utf-8")
    result = await service.reconcile_memory()
    assert result["state"] == "completed"
    profile = service.global_profile()
    assert profile.preferences[0].value == "先展示例题"
    assert profile.preferences[0].state == "explicit"
    assert entry_id in profile.preferences[0].evidence_refs
    memory_path.write_text("# Preferences\n", encoding="utf-8")
    await service.reconcile_memory()
    assert service.global_profile().preferences == []


@pytest.mark.asyncio
async def test_concept_ids_do_not_merge_when_labels_match(learner_service):
    service, _ = learner_service
    subject = _subject()
    for event_id, concept_id in [("a", "algebra:function"), ("b", "calculus:function")]:
        await service.record_event(LearnerEvent(
            event_id=event_id, event_type="quiz_answer", subject=subject,
            concept_id=concept_id, concept_label="函数", observation="correct",
            evidence_refs=[f"question:{event_id}"], occurred_at="2026-07-29T00:00:00+00:00",
        ), trusted=True)
    assert {item.concept_id for item in service.subject_profile(subject.subject_id).concept_signals} == {"algebra:function", "calculus:function"}


@pytest.mark.asyncio
async def test_bkt_strict_multi_subject_event_matrix_keeps_subjects_isolated(learner_service):
    service, _ = learner_service
    mathematics = _subject_ref("mathematics", "数学", "函数")
    physics = _subject_ref("physics", "物理", "力学")
    biology = _subject_ref("biology", "生物", "植物生理")

    await service.record_event(LearnerEvent(
        event_id="math-slope-q1", event_type="quiz_answer", subject=mathematics,
        concept_id="rate", concept_label="变化率", module_id="linear-functions",
        observation="correct", confidence=.9, evidence_refs=["math:quiz:1"],
        occurred_at="2026-07-29T00:01:00+00:00",
    ), trusted=True)
    await service.record_event(LearnerEvent(
        event_id="math-slope-q2", event_type="quiz_answer", subject=mathematics,
        concept_id="rate", concept_label="变化率", module_id="linear-functions",
        observation="correct", confidence=.9, evidence_refs=["math:quiz:2"],
        occurred_at="2026-07-29T00:02:00+00:00",
    ), trusted=True)
    # Courseware participation may add engagement evidence, but must not move
    # mastery probability or verified-observation count.
    await service.record_event(LearnerEvent(
        event_id="math-slope-courseware", event_type="courseware_outcome", subject=mathematics,
        concept_id="rate", concept_label="变化率", module_id="linear-functions",
        observation="engaged", confidence=.8, evidence_refs=["math:courseware:1"],
        occurred_at="2026-07-29T00:03:00+00:00",
    ), trusted=True)

    await service.record_event(LearnerEvent(
        event_id="physics-force-q1", event_type="quiz_answer", subject=physics,
        concept_id="force", concept_label="力", module_id="mechanics",
        observation="incorrect", confidence=.9, evidence_refs=["physics:quiz:1"],
        occurred_at="2026-07-29T00:01:00+00:00",
    ), trusted=True)
    await service.record_event(LearnerEvent(
        event_id="biology-rate-card", event_type="flashcard_review", subject=biology,
        # Same concept_id as the mathematics concept: subject scoping must keep
        # these as separate BKT states.
        concept_id="rate", concept_label="光合速率", module_id="photosynthesis",
        observation="uncertain", confidence=.7, evidence_refs=["biology:card:1"],
        occurred_at="2026-07-29T00:01:00+00:00",
    ), trusted=True)

    math_expected = _expected_bkt(
        _expected_bkt(.2, correct=True, transition=.15, guess=.20, slip=.10, weight=1.0),
        correct=True, transition=.15, guess=.20, slip=.10, weight=1.0,
    )
    physics_expected = _expected_bkt(.2, correct=False, transition=.15, guess=.20, slip=.10, weight=1.0)
    biology_expected = _expected_bkt(.2, correct=False, transition=.08, guess=.28, slip=.16, weight=.55)

    math_signal = service.subject_profile("mathematics").concept_signals[0]
    physics_signal = service.subject_profile("physics").concept_signals[0]
    biology_signal = service.subject_profile("biology").concept_signals[0]

    assert math_signal.concept_id == "rate"
    assert math_signal.label == "变化率"
    assert math_signal.module_id == "linear-functions"
    assert math_signal.mastery_probability == pytest.approx(math_expected)
    assert math_signal.verified_observation_count == 2
    assert math_signal.observation_count == 3
    assert math_signal.support_level == "supported"
    assert math_signal.last_observation_source == "courseware_outcome"

    assert physics_signal.concept_id == "force"
    assert physics_signal.mastery_probability == pytest.approx(physics_expected)
    assert physics_signal.verified_observation_count == 1
    assert physics_signal.support_level == "needs_support"

    assert biology_signal.concept_id == "rate"
    assert biology_signal.label == "光合速率"
    assert biology_signal.mastery_probability == pytest.approx(biology_expected)
    assert biology_signal.verified_observation_count == 1
    assert biology_signal.support_level == "needs_support"
    assert biology_signal.last_observation_source == "flashcard_review"

    subjects = {profile.subject.subject_id for profile in service.subjects() if profile.subject}
    assert subjects == {"mathematics", "physics", "biology"}

    math_context = service.build_context(purpose="quiz", subject=mathematics)
    physics_context = service.build_context(purpose="quiz", subject=physics)
    biology_context = service.build_context(purpose="flashcards", subject=biology)
    assert [item.concept_id for item in math_context.relevant_concept_signals] == ["rate"]
    assert math_context.relevant_concept_signals[0].label == "变化率"
    assert [item.concept_id for item in physics_context.relevant_concept_signals] == ["force"]
    assert [item.label for item in biology_context.relevant_concept_signals] == ["光合速率"]


@pytest.mark.asyncio
async def test_bkt_strict_delete_evidence_rebuilds_only_the_affected_subject(learner_service):
    service, _ = learner_service
    mathematics = _subject_ref("mathematics", "数学", "函数")
    physics = _subject_ref("physics", "物理", "力学")

    for index in (1, 2):
        await service.record_event(LearnerEvent(
            event_id=f"math-limit-q{index}", event_type="quiz_answer", subject=mathematics,
            concept_id="limits", concept_label="极限", observation="correct",
            evidence_refs=[f"math:quiz:{index}"],
            occurred_at=f"2026-07-29T00:0{index}:00+00:00",
        ), trusted=True)
    await service.record_event(LearnerEvent(
        event_id="physics-force-q1", event_type="quiz_answer", subject=physics,
        concept_id="force", concept_label="力", observation="incorrect",
        evidence_refs=["physics:quiz:1"], occurred_at="2026-07-29T00:01:00+00:00",
    ), trusted=True)

    before_math = service.subject_profile("mathematics").concept_signals[0]
    before_physics = service.subject_profile("physics").concept_signals[0]
    two_correct_expected = _expected_bkt(
        _expected_bkt(.2, correct=True, transition=.15, guess=.20, slip=.10, weight=1.0),
        correct=True, transition=.15, guess=.20, slip=.10, weight=1.0,
    )
    assert before_math.mastery_probability == pytest.approx(two_correct_expected)
    assert before_math.verified_observation_count == 2

    assert await service.delete_evidence("math-limit-q2") is True

    after_math = service.subject_profile("mathematics").concept_signals[0]
    after_physics = service.subject_profile("physics").concept_signals[0]
    one_correct_expected = _expected_bkt(.2, correct=True, transition=.15, guess=.20, slip=.10, weight=1.0)

    assert after_math.mastery_probability == pytest.approx(one_correct_expected)
    assert after_math.verified_observation_count == 1
    assert after_math.support_level == "developing"
    assert after_math.evidence_refs == ["math:quiz:1"]

    assert after_physics.mastery_probability == before_physics.mastery_probability
    assert after_physics.verified_observation_count == before_physics.verified_observation_count
    assert after_physics.evidence_refs == before_physics.evidence_refs
    assert {item.signal_id for item in service.evidence(subject_id="mathematics")} == {"math-limit-q1"}
    assert {item.signal_id for item in service.evidence(subject_id="physics")} == {"physics-force-q1"}


@pytest.mark.asyncio
async def test_memory_reconcile_replaces_an_edited_l3_entry(learner_service):
    service, _ = learner_service
    from traittutor.services.memory.document import Document, Entry, serialize

    path = service._root().parent / "L3" / "preferences.md"; path.parent.mkdir(parents=True)
    entry_id = "m_01ARZ3NDEKTSV4RRFFQ69G5FAV"
    path.write_text(serialize(Document(title="Preferences", sections=[("Preferences", [Entry(entry_id, "Preferences", "先举例", ["chat:1"])])])), encoding="utf-8")
    await service.reconcile_memory()
    path.write_text(serialize(Document(title="Preferences", sections=[("Preferences", [Entry(entry_id, "Preferences", "先解释定义", ["chat:2"])])])), encoding="utf-8")
    await service.reconcile_memory()
    preferences = service.global_profile().preferences
    assert [item.value for item in preferences] == ["先解释定义"]
    assert preferences[0].evidence_refs == [entry_id, "chat:2"]


@pytest.mark.asyncio
async def test_subject_correction_reassigns_existing_evidence(learner_service):
    service, _ = learner_service
    old = _subject()
    await service.record_event(LearnerEvent(
        event_id="physics-event", event_type="quiz_answer", subject=old,
        concept_id="motion", concept_label="运动", observation="incorrect",
        evidence_refs=["question:1"], occurred_at="2026-07-29T00:00:00+00:00",
    ), trusted=True)
    replacement = SubjectRef(subject_id="physics", label="物理", path=["科学", "物理"], confidence=1, source="user", confirmed=True)
    profile = await service.correct_subject(old.subject_id, replacement)
    assert profile.subject and profile.subject.subject_id == "physics"
    assert service.subject_profile(old.subject_id).subject is None
    assert service.evidence(subject_id="physics")[0].subject_refs[0].label == "物理"


def test_unknown_subject_has_no_profile(learner_service):
    service, _ = learner_service
    assert service.subject_profile("does-not-exist").subject is None
