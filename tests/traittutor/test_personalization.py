from __future__ import annotations

from types import SimpleNamespace

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
