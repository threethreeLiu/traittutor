from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from traittutor.api.routers import learning_packs as learning_packs_router
from traittutor.api.routers.learning_packs import (
    ComponentInteractionRequest,
    UpdatePackRequest,
    _record_component_learning_event,
    _record_pack_learning_events,
    record_learning_component_event,
    update_learning_pack,
)
from traittutor.api.routers.traittutor_generate import (
    _record_canonical_generation_quiz_answer,
)
from traittutor.learning.event_chain import CanonicalAnswerEventChain
from traittutor.learning.models import (
    ErrorRecordStatus,
    KnowledgePoint,
    KnowledgeType,
    LearningModule,
    LearningProgress,
)
from traittutor.learning.service import LearningService
from traittutor.learning.storage import LearningStore
from traittutor.learning_model import KnowledgeStateKey, LearnerEventLedger, is_strong_evidence
from traittutor.multi_user.context import reset_current_user, set_current_user
from traittutor.multi_user.models import CurrentUser
from traittutor.multi_user.paths import scope_for_user
from traittutor.personalization.models import SubjectRef


class _Recorder:
    def __init__(self) -> None:
        self.events: list[object] = []

    async def record_event(self, event: object, *, trusted: bool) -> list[object]:
        assert trusted is True
        self.events.append(event)
        return []


def _chain(tmp_path):
    recorder = _Recorder()
    chain = CanonicalAnswerEventChain(
        LearnerEventLedger(tmp_path / "events.json"),
        personalization_service_factory=lambda: recorder,
    )
    return chain, recorder


def test_assessment_reattempt_route_is_not_registered() -> None:
    paths = {route.path for route in learning_packs_router.router.routes}

    assert "/{pack_id}/plans/{plan_id}/components/{component_id}/reattempt" not in paths


def _progress(book_id: str) -> LearningProgress:
    return LearningProgress(
        book_id=book_id,
        modules=[
            LearningModule(
                id="module-1",
                name="Linear equations",
                order=1,
                knowledge_points=[
                    KnowledgePoint(
                        id="kc1",
                        name="Solve equations",
                        type=KnowledgeType.PROCEDURE,
                        module_id="module-1",
                    )
                ],
            )
        ],
        knowledge_types={"kc1": KnowledgeType.PROCEDURE},
    )


def _bind_pack(
    pack: dict[str, object],
    *,
    learning_path_id: str = "book-1",
    allowed_kc_ids: tuple[str, ...] = ("kc1",),
) -> dict[str, object]:
    """Attach the same explicit server-authored link used by Pack grading."""
    pack["learning_path_bindings"] = [
        {
            "binding_id": "binding-1",
            "revision": 1,
            "status": "active",
            "owner_id": "u1",
            "learning_path_id": learning_path_id,
            "subject_id": "math",
            "allowed_kc_ids": list(allowed_kc_ids),
            "graph_fingerprint": "graph-1",
            "graph_version": 1,
            "linked_at": "2026-08-10T00:00:00+00:00",
        }
    ]
    pack["active_learning_path_binding_revision"] = 1
    return pack


@pytest.fixture
def learner_user():
    token = set_current_user(
        CurrentUser(
            id="u1",
            username="learner",
            role="user",
            scope=scope_for_user("u1", is_admin=False),
        )
    )
    try:
        yield
    finally:
        reset_current_user(token)


def test_standalone_quiz_flag_on_records_once_before_bkt(
    tmp_path, monkeypatch, learner_user
) -> None:
    chain, recorder = _chain(tmp_path)
    result = SimpleNamespace(
        events=[
            {
                "type": "material_abstraction_ready",
                "data": {"subject_ref": {"subject_id": "math"}},
            }
        ]
    )
    item = {
        "question_id": "q1",
        "node_id": "linear-equations",
        "correct_answer": "B",
    }

    for _ in range(2):
        _record_canonical_generation_quiz_answer(
            result=result,
            item=item,
            correct=True,
            attempt_id="attempt-1",
            chain=chain,
        )

    assert len(chain.ledger) == 1
    state = chain.rebuild_bkt().get(
        KnowledgeStateKey(user_id="u1", subject_id="math", kc_id="linear-equations")
    )
    assert state is not None
    assert state.verified_observation_count == 1
    assert len(recorder.events) == 1


def test_standalone_quiz_missing_server_kc_is_pending_not_bkt(
    tmp_path, monkeypatch, learner_user
) -> None:
    chain, recorder = _chain(tmp_path)

    _record_canonical_generation_quiz_answer(
        result=SimpleNamespace(events=[]),
        item={"question_id": "q1", "correct_answer": "B"},
        correct=True,
        attempt_id="attempt-1",
        chain=chain,
    )

    event = list(chain.ledger)[0]
    assert is_strong_evidence(event) is False
    assert event.attribution_status == "attribution_pending"
    assert chain.rebuild_bkt().all_for(user_id="u1", subject_id="math") == []
    assert recorder.events == []


def test_standalone_attempt_id_rejects_changed_verdict(tmp_path, monkeypatch, learner_user) -> None:
    chain, _recorder = _chain(tmp_path)
    result = SimpleNamespace(events=[{"data": {"subject_ref": {"subject_id": "math"}}}])
    item = {"question_id": "q1", "node_id": "kc1", "correct_answer": "B"}
    _record_canonical_generation_quiz_answer(
        result=result,
        item=item,
        correct=True,
        attempt_id="attempt-1",
        chain=chain,
    )

    with pytest.raises(ValueError, match="cannot be reused"):
        _record_canonical_generation_quiz_answer(
            result=result,
            item=item,
            correct=False,
            attempt_id="attempt-1",
            chain=chain,
        )

    assert len(chain.ledger) == 1


def test_standalone_quiz_projects_existing_path_once(tmp_path, monkeypatch, learner_user) -> None:
    chain, _recorder = _chain(tmp_path)
    store = LearningStore(tmp_path / "progress")
    service = LearningService(
        store,
        event_chain=chain,
        resume_canonical_derivations=False,
    )
    store.save(_progress("book-1"))
    result = SimpleNamespace(events=[{"data": {"subject_ref": {"subject_id": "math"}}}])
    item = {"question_id": "q1", "node_id": "kc1", "correct_answer": "B"}

    for _ in range(2):
        _record_canonical_generation_quiz_answer(
            result=result,
            item=item,
            correct=False,
            attempt_id="standalone-attempt-1",
            chain=chain,
            learning_service=service,
            learning_path_id="book-1",
            user_answer="A",
        )

    persisted = store.load("book-1")
    assert persisted is not None
    assert len(persisted.quiz_attempts) == 1
    assert len(persisted.error_records) == 1
    assert persisted.error_records[0].source_event_ids == [list(chain.ledger)[0].event_id]
    assert persisted.review_queue


def test_projection_rejects_subject_mismatched_existing_path(
    tmp_path, monkeypatch, learner_user
) -> None:
    chain, _recorder = _chain(tmp_path)
    store = LearningStore(tmp_path / "progress")
    service = LearningService(
        store,
        event_chain=chain,
        resume_canonical_derivations=False,
    )
    progress = _progress("book-1")
    progress.subject_id = "physics"
    store.save(progress)

    _record_canonical_generation_quiz_answer(
        result=SimpleNamespace(events=[{"data": {"subject_ref": {"subject_id": "math"}}}]),
        item={"question_id": "q1", "node_id": "kc1", "correct_answer": "B"},
        correct=False,
        attempt_id="mismatched-subject-attempt",
        chain=chain,
        learning_service=service,
        learning_path_id="book-1",
        user_answer="A",
    )

    persisted = store.load("book-1")
    assert persisted is not None
    assert persisted.quiz_attempts == []
    assert persisted.error_records == []


def test_pack_quiz_and_component_project_error_repair_once(tmp_path, learner_user) -> None:
    chain, _recorder = _chain(tmp_path)
    store = LearningStore(tmp_path / "progress")
    service = LearningService(
        store,
        event_chain=chain,
        resume_canonical_derivations=False,
    )
    store.save(_progress("pack-1"))
    subject = SubjectRef(
        subject_id="math", label="Math", confidence=1, source="user", confirmed=True
    )
    pack = _bind_pack(
        {
            "pack_id": "pack-1",
            "material": {},
            "artifacts": {
                "quiz": [
                    {
                        "verified_generation_id": "generation-1",
                        "subject_ref": subject.model_dump(),
                        "items": [
                            {
                                "question_id": "q1",
                                "node_id": "kc1",
                                "node_name": "Solve",
                                "correct_answer": "B",
                            }
                        ],
                    }
                ]
            },
        },
        learning_path_id="pack-1",
    )
    patch = {
        "quiz_attempt": {
            "attempt_id": "quiz-session-1",
            "submitted_at": "2026-08-10T12:00:00+00:00",
            "answers": {"0": "A"},
            "checked": [0],
        }
    }

    asyncio.run(_record_pack_learning_events(pack, patch, chain=chain, learning_service=service))
    asyncio.run(_record_pack_learning_events(pack, patch, chain=chain, learning_service=service))
    after_wrong = store.load("pack-1")
    assert after_wrong is not None
    assert len(after_wrong.quiz_attempts) == 1
    assert len(after_wrong.error_records) == 1
    assert after_wrong.error_records[0].status == ErrorRecordStatus.OPEN
    assert after_wrong.review_queue

    plan = {
        "plan_id": "plan-1",
        "subject_ref": subject.model_dump(),
    }
    component = {
        "component_id": "assessment-1",
        "component_type": "guided_practice",
        "concept_refs": ["kc1"],
    }
    request = ComponentInteractionRequest(
        action="complete",
        observation="correct",
        question_id="q1",
        answer="B",
        output_ref="generation-1",
    )
    asyncio.run(
        _record_component_learning_event(
            pack,
            plan,
            component,
            request,
            "component-attempt-1",
            chain=chain,
            learning_service=service,
        )
    )
    asyncio.run(
        _record_component_learning_event(
            pack,
            plan,
            component,
            request,
            "component-attempt-1",
            chain=chain,
            learning_service=service,
        )
    )
    repaired = store.load("pack-1")
    assert repaired is not None
    assert len(repaired.quiz_attempts) == 2
    assert len(repaired.error_records) == 1
    assert repaired.error_records[0].status == ErrorRecordStatus.REPAIRED
    assert len(repaired.error_records[0].retry_history) == 1


def test_revealed_component_question_cannot_create_second_strong_event(
    tmp_path, learner_user
) -> None:
    chain, _recorder = _chain(tmp_path)
    subject = SubjectRef(
        subject_id="math",
        label="Math",
        confidence=1,
        source="user",
        confirmed=True,
    )
    pack = _bind_pack(
        {
            "pack_id": "pack-revealed",
            "artifacts": {
                "quiz": [
                    {
                        "verified_generation_id": "generation-1",
                        "subject_ref": subject.model_dump(),
                        "items": [
                            {
                                "question_id": "q1",
                                "node_id": "kc1",
                                "correct_answer": "B",
                            }
                        ],
                    }
                ]
            },
            "component_progress": {
                "old-plan": {
                    "events": [
                        {
                            "event_id": "first-attempt",
                            "question_id": "q1",
                            "observation": "incorrect",
                        }
                    ]
                }
            },
        },
        learning_path_id="pack-revealed",
    )
    plan = {"plan_id": "new-plan", "subject_ref": subject.model_dump()}
    component = {
        "component_id": "assessment-new",
        "component_type": "guided_practice",
        "concept_refs": ["kc1"],
    }
    recorded = asyncio.run(
        _record_component_learning_event(
            pack,
            plan,
            component,
            ComponentInteractionRequest(
                action="complete",
                observation="correct",
                question_id="q1",
                answer="B",
                output_ref="generation-1",
            ),
            "second-attempt",
            chain=chain,
        )
    )

    assert recorded is False
    event = next(iter(chain.ledger))
    assert not is_strong_evidence(event)
    assert chain.rebuild_bkt().all_for(user_id=event.user_id, subject_id="math") == []


def test_learning_pack_batch_uses_one_canonical_event_per_question(tmp_path, learner_user) -> None:
    chain, recorder = _chain(tmp_path)
    subject = SubjectRef(
        subject_id="math",
        label="Math",
        confidence=1,
        source="user",
        confirmed=True,
    )

    pack = _bind_pack(
        {
            "pack_id": "pack-1",
            "material": {},
            "artifacts": {
                "quiz": [
                    {
                        "verified_generation_id": "generation-1",
                        "subject_ref": subject.model_dump(),
                        "items": [
                            {
                                "question_id": "q1",
                                "node_id": "kc1",
                                "node_name": "One",
                                "correct_answer": "A",
                            },
                            {
                                "question_id": "q2",
                                "node_id": "kc2",
                                "node_name": "Two",
                                "correct_answer": "B",
                            },
                        ],
                    }
                ]
            },
        },
        allowed_kc_ids=("kc1", "kc2"),
    )
    patch = {
        "quiz_attempt": {
            "attempt_id": "quiz-session-1",
            "submitted_at": "2026-08-09T12:00:00+00:00",
            "answers": {"0": "A", "1": "B"},
            "checked": [0, 1],
        }
    }

    asyncio.run(_record_pack_learning_events(pack, patch, chain=chain))

    assert len(chain.ledger) == 2
    assert {event.item_id for event in chain.ledger} == {"q1", "q2"}
    assert len(recorder.events) == 2


def test_pack_save_reuses_standalone_question_attempt_without_double_count(
    tmp_path, learner_user
) -> None:
    chain, recorder = _chain(tmp_path)
    result = SimpleNamespace(events=[{"data": {"subject_ref": {"subject_id": "math"}}}])
    item = {
        "question_id": "q1",
        "node_id": "kc1",
        "node_name": "One",
        "correct_answer": "A",
    }
    _record_canonical_generation_quiz_answer(
        result=result,
        item=item,
        correct=True,
        attempt_id="question-attempt-1",
        chain=chain,
    )

    pack = _bind_pack(
        {
            "pack_id": "pack-1",
            "material": {},
            "artifacts": {
                "quiz": [
                    {
                        "verified_generation_id": "generation-1",
                        "items": [item],
                    }
                ]
            },
        }
    )
    patch = {
        "quiz_attempt": {
            "attempt_id": "quiz-session-1",
            "attempt_ids": {"0": "question-attempt-1"},
            "submitted_at": "2026-08-09T12:00:00+00:00",
            "answers": {"0": "A"},
            "checked": [0],
        }
    }

    asyncio.run(_record_pack_learning_events(pack, patch, chain=chain))

    assert len(chain.ledger) == 1
    state = chain.rebuild_bkt().get(KnowledgeStateKey(user_id="u1", subject_id="math", kc_id="kc1"))
    assert state is not None and state.verified_observation_count == 1
    assert len(recorder.events) == 1


def test_component_assessment_uses_canonical_chain_only(tmp_path, learner_user) -> None:
    chain, recorder = _chain(tmp_path)
    pack = _bind_pack(
        {
            "pack_id": "pack-1",
            "artifacts": {
                "quiz": [
                    {
                        "verified_generation_id": "generation-1",
                        "items": [
                            {
                                "question_id": "q1",
                                "node_id": "kc1",
                                "correct_answer": "B",
                            }
                        ],
                    }
                ]
            },
        }
    )
    plan = {
        "plan_id": "plan-1",
        "subject_ref": {
            "subject_id": "math",
            "label": "Math",
            "confidence": 1,
            "source": "user",
            "confirmed": True,
        },
    }
    component = {
        "component_id": "assessment-1",
        "component_type": "guided_practice",
        "concept_refs": ["kc1"],
    }
    request = ComponentInteractionRequest(
        action="complete",
        observation="correct",
        confidence=0.5,
        question_id="q1",
        answer="B",
        output_ref="generation-1",
        concept_id="browser-forged",
    )

    first = asyncio.run(
        _record_component_learning_event(
            pack, plan, component, request, "component-attempt-1", chain=chain
        )
    )
    replay = asyncio.run(
        _record_component_learning_event(
            pack, plan, component, request, "component-attempt-1", chain=chain
        )
    )

    assert first is True and replay is True
    assert len(chain.ledger) == 1
    event = list(chain.ledger)[0]
    assert event.kc_ids == ("kc1",)
    assert event.user_id == "u1"
    assert len(recorder.events) == 1


@pytest.mark.asyncio
async def test_pack_route_does_not_swallow_canonical_append_failure(
    monkeypatch, learner_user
) -> None:
    pack = {"pack_id": "pack-1", "artifacts": {"quiz": []}}
    monkeypatch.setattr(learning_packs_router.learning_packs, "update_pack", lambda *_a: pack)

    async def fail_append(*_args, **_kwargs):
        raise OSError("ledger unavailable")

    monkeypatch.setattr(learning_packs_router, "_record_pack_learning_events", fail_append)

    with pytest.raises(OSError, match="ledger unavailable"):
        await update_learning_pack(
            "pack-1",
            UpdatePackRequest(quiz_attempt={"attempt_id": "attempt-1"}),
        )


@pytest.mark.asyncio
async def test_component_route_does_not_swallow_canonical_append_failure(
    monkeypatch, learner_user
) -> None:
    component = {
        "component_id": "lesson-1",
        "component_type": "concept_explanation",
    }
    pack = {"pack_id": "pack-1"}
    plan = {"plan_id": "plan-1", "components": [component]}
    monkeypatch.setattr(learning_packs_router.learning_packs, "get_pack", lambda *_a: pack)
    monkeypatch.setattr(
        learning_packs_router.learning_packs,
        "get_component_plan",
        lambda *_a: plan,
    )
    monkeypatch.setattr(
        learning_packs_router.learning_packs,
        "record_component_event",
        lambda *_a, **kwargs: kwargs["before_mutation"](pack, plan, component) or (pack, component),
    )
    monkeypatch.setattr(
        learning_packs_router.learning_packs,
        "validate_component_event",
        lambda *_a: (pack, component),
    )

    def fail_append(*_args, **_kwargs):
        raise OSError("ledger unavailable")

    monkeypatch.setattr(
        learning_packs_router,
        "_record_component_learning_event_sync",
        fail_append,
    )

    with pytest.raises(OSError, match="ledger unavailable"):
        await record_learning_component_event(
            "pack-1",
            "plan-1",
            "lesson-1",
            ComponentInteractionRequest(action="complete", replan=False),
        )


@pytest.mark.asyncio
async def test_incorrect_component_persists_private_canonical_repair_provenance(
    tmp_path, monkeypatch, learner_user
) -> None:
    """A later repair review can recover only the actual canonical source event."""
    chain, _recorder = _chain(tmp_path)
    monkeypatch.setattr(learning_packs_router, "CanonicalAnswerEventChain", lambda: chain)
    monkeypatch.setattr(
        learning_packs_router, "_validate_component_output_reference", lambda _r: None
    )
    component = {
        "component_id": "assessment-1",
        "component_type": "guided_practice",
        "concept_refs": ["kc1"],
    }
    pack = _bind_pack(
        {
            "pack_id": "pack-1",
            "artifacts": {
                "quiz": [
                    {
                        "verified_generation_id": "generation-1",
                        "items": [
                            {
                                "question_id": "q1",
                                "node_id": "kc1",
                                "question": "Choose B",
                                "correct_answer": "B",
                            }
                        ],
                    }
                ]
            },
        }
    )
    plan = {
        "plan_id": "plan-1",
        "subject_ref": {
            "subject_id": "math",
            "label": "Math",
            "confidence": 1,
            "source": "user",
            "confirmed": True,
        },
        "components": [component],
    }
    captured: dict[str, object] = {}
    monkeypatch.setattr(learning_packs_router.learning_packs, "get_pack", lambda _id: pack)
    monkeypatch.setattr(
        learning_packs_router.learning_packs,
        "get_component_plan",
        lambda _pack_id, _plan_id: plan,
    )

    def capture_component_event(*args, **kwargs):
        captured["component_event"] = args[-1]
        kwargs["before_mutation"](pack, plan, component)
        return pack, component

    monkeypatch.setattr(
        learning_packs_router.learning_packs,
        "record_component_event",
        capture_component_event,
    )
    monkeypatch.setattr(
        learning_packs_router.learning_packs,
        "validate_component_event",
        lambda *_args: (pack, component),
    )

    def capture_repair(*_args, **kwargs):
        captured.update(kwargs)
        return {"repair_id": "repair-1"}

    monkeypatch.setattr(learning_packs_router.learning_packs, "create_repair", capture_repair)

    await record_learning_component_event(
        "pack-1",
        "plan-1",
        "assessment-1",
        ComponentInteractionRequest(
            action="complete",
            observation="incorrect",
            confidence=0.9,
            question_id="q1",
            answer="A",
            output_ref="generation-1",
            event_id="component-attempt-1",
            replan=False,
        ),
    )

    source = next(iter(chain.ledger))
    assert captured["canonical_source_event_id"] == source.event_id
    assert captured["review_subject_id"] == "math"
    assert captured["review_kc_id"] == "kc1"
    assert captured["retry_question_id"] == "q1"
    # Correctness is evidence; completion is workflow state. A wrong final
    # answer still closes the fully submitted assessment so the next step can
    # unlock without claiming mastery.
    assert captured["component_event"]["action"] == "complete"
