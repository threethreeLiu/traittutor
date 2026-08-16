"""Canonical projection coverage for private deep-question submissions."""

from __future__ import annotations

import asyncio

import pytest

from traittutor.api.routers import sessions as sessions_router
from traittutor.core.stream import StreamEvent, StreamEventType
from traittutor.learning.event_chain import CanonicalAnswerEventChain
from traittutor.learning.models import (
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
from traittutor.services.session.sqlite_store import SQLiteSessionStore
from traittutor.services.session.turn_runtime import TurnRuntimeManager, _TurnExecution


class _PersonalizationRecorder:
    def __init__(self) -> None:
        self.events: list[object] = []

    async def record_event(self, event: object, *, trusted: bool) -> list[object]:
        assert trusted is True
        self.events.append(event)
        return []


@pytest.fixture
def learner_user():
    token = set_current_user(
        CurrentUser(
            id="session-learner",
            username="session-learner",
            role="user",
            scope=scope_for_user("session-learner", is_admin=False),
        )
    )
    try:
        yield
    finally:
        reset_current_user(token)


def _chain(tmp_path):
    recorder = _PersonalizationRecorder()
    return (
        CanonicalAnswerEventChain(
            LearnerEventLedger(tmp_path / "events.json"),
            personalization_service_factory=lambda: recorder,
        ),
        recorder,
    )


def _progress(*, subject_id: str = "math") -> LearningProgress:
    return LearningProgress(
        book_id="path-math",
        subject_id=subject_id,
        modules=[
            LearningModule(
                id="module-1",
                name="Equations",
                order=1,
                knowledge_points=[
                    KnowledgePoint(
                        id="kc-linear",
                        name="Linear equations",
                        type=KnowledgeType.PROCEDURE,
                        module_id="module-1",
                    )
                ],
            )
        ],
        knowledge_types={"kc-linear": KnowledgeType.PROCEDURE},
    )


async def _seed_server_item(
    store: SQLiteSessionStore,
    *,
    learning_path_id: str = "path-math",
    subject_id: str = "math",
    kc_id: str = "kc-linear",
) -> str:
    await store.create_session(session_id="session-1")
    turn = await store.create_turn("session-1", capability="deep_question")
    await store.upsert_server_quiz_items(
        "session-1",
        turn["id"],
        [
            {
                "question_id": "q-1",
                "question": "Solve x + 2 = 4.",
                "question_type": "short",
                "correct_answer": "2",
                "subject_id": subject_id,
                "kc_id": kc_id,
                "learning_path_id": learning_path_id,
            }
        ],
    )
    return str(turn["id"])


@pytest.mark.asyncio
async def test_server_held_quiz_projects_existing_path_once(
    tmp_path, monkeypatch, learner_user
) -> None:
    session_store = SQLiteSessionStore(tmp_path / "sessions.sqlite")
    turn_id = await _seed_server_item(session_store)
    assert (await session_store.get_server_quiz_item("session-1", turn_id, "q-1"))[
        "learning_path_id"
    ] == "path-math"
    monkeypatch.setattr(sessions_router, "get_sqlite_session_store", lambda: session_store)

    chain, recorder = _chain(tmp_path)
    progress_store = LearningStore(tmp_path / "progress")
    progress_store.save(_progress())
    service = LearningService(
        progress_store,
        event_chain=chain,
        resume_canonical_derivations=False,
    )

    for _ in range(2):
        result = await sessions_router._grade_server_held_quiz_item(
            session_id="session-1",
            turn_id=turn_id,
            question_id="q-1",
            answer="1",
            attempt_id="session-attempt-1",
            chain=chain,
            learning_service=service,
        )
        assert result["correct"] is False
    await asyncio.sleep(0)

    event = list(chain.ledger)[0]
    assert is_strong_evidence(event)
    assert event.learning_path_id == "path-math"
    persisted = progress_store.load("path-math")
    assert persisted is not None
    assert len(persisted.quiz_attempts) == 1
    assert len(persisted.error_records) == 1
    assert persisted.error_records[0].source_event_ids == [event.event_id]
    assert persisted.review_queue
    state = chain.rebuild_bkt().get(
        KnowledgeStateKey(user_id="session-learner", subject_id="math", kc_id="kc-linear")
    )
    assert state is not None and state.verified_observation_count == 1
    assert len(recorder.events) == 1


@pytest.mark.asyncio
async def test_server_held_quiz_without_explicit_path_is_pending_not_bkt(
    tmp_path, monkeypatch, learner_user
) -> None:
    session_store = SQLiteSessionStore(tmp_path / "sessions.sqlite")
    turn_id = await _seed_server_item(session_store, learning_path_id="")
    monkeypatch.setattr(sessions_router, "get_sqlite_session_store", lambda: session_store)
    chain, recorder = _chain(tmp_path)
    progress_store = LearningStore(tmp_path / "progress")
    progress_store.save(_progress())
    service = LearningService(
        progress_store,
        event_chain=chain,
        resume_canonical_derivations=False,
    )

    await sessions_router._grade_server_held_quiz_item(
        session_id="session-1",
        turn_id=turn_id,
        question_id="q-1",
        answer="2",
        attempt_id="unbound-attempt-1",
        chain=chain,
        learning_service=service,
    )

    event = list(chain.ledger)[0]
    assert is_strong_evidence(event) is False
    assert event.attribution_status == "attribution_pending"
    assert event.learning_path_id is None
    assert chain.rebuild_bkt().all_for(user_id="session-learner", subject_id="math") == []
    assert progress_store.load("path-math").quiz_attempts == []  # type: ignore[union-attr]
    assert recorder.events == []


@pytest.mark.asyncio
async def test_server_held_quiz_rejects_subject_mismatched_path(
    tmp_path, monkeypatch, learner_user
) -> None:
    session_store = SQLiteSessionStore(tmp_path / "sessions.sqlite")
    turn_id = await _seed_server_item(session_store, subject_id="math")
    monkeypatch.setattr(sessions_router, "get_sqlite_session_store", lambda: session_store)
    chain, recorder = _chain(tmp_path)
    progress_store = LearningStore(tmp_path / "progress")
    progress_store.save(_progress(subject_id="physics"))
    service = LearningService(
        progress_store,
        event_chain=chain,
        resume_canonical_derivations=False,
    )

    await sessions_router._grade_server_held_quiz_item(
        session_id="session-1",
        turn_id=turn_id,
        question_id="q-1",
        answer="2",
        attempt_id="cross-subject-attempt-1",
        chain=chain,
        learning_service=service,
    )

    event = list(chain.ledger)[0]
    assert is_strong_evidence(event) is False
    assert chain.rebuild_bkt().all_for(user_id="session-learner", subject_id="math") == []
    persisted = progress_store.load("path-math")
    assert persisted is not None and persisted.quiz_attempts == []
    assert recorder.events == []


@pytest.mark.asyncio
async def test_stream_quiz_discards_model_emitted_path_provenance(tmp_path, learner_user) -> None:
    """A model cannot bind its question to an arbitrary learning path."""
    store = SQLiteSessionStore(tmp_path / "sessions.sqlite")
    await store.create_session(session_id="session-1")
    turn = await store.create_turn("session-1", capability="deep_question")
    runtime = TurnRuntimeManager(store=store)
    execution = _TurnExecution(
        turn_id=str(turn["id"]),
        session_id="session-1",
        capability="deep_question",
        payload={"config": {}},
    )
    event = StreamEvent(
        type=StreamEventType.CONTENT,
        metadata={
            "qa_pair": {
                "question_id": "q-1",
                "question": "Solve x + 2 = 4.",
                "correct_answer": "2",
                "subject_id": "math",
                "kc_id": "kc-linear",
                "learning_path_id": "model-invented-path",
            }
        },
    )

    await runtime._capture_and_project_quiz_event(execution, event)

    persisted = await store.get_server_quiz_item("session-1", str(turn["id"]), "q-1")
    assert persisted is not None
    assert persisted["learning_path_id"] == ""


@pytest.mark.asyncio
async def test_stream_quiz_persists_only_verified_turn_path(
    tmp_path, monkeypatch, learner_user
) -> None:
    """The explicit server turn context is checked against the current path."""
    store = SQLiteSessionStore(tmp_path / "sessions.sqlite")
    await store.create_session(session_id="session-1")
    turn = await store.create_turn("session-1", capability="deep_question")
    runtime = TurnRuntimeManager(store=store)
    execution = _TurnExecution(
        turn_id=str(turn["id"]),
        session_id="session-1",
        capability="deep_question",
        payload={"config": {"learning_pack_id": "path-math"}},
    )

    calls: list[dict[str, str]] = []

    class _VerifiedService:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def has_existing_canonical_target(self, **kwargs: str) -> bool:
            calls.append(kwargs)
            return kwargs == {
                "user_id": "session-learner",
                "subject_id": "math",
                "kc_id": "kc-linear",
                "learning_path_id": "path-math",
            }

    import traittutor.learning.service as learning_service_module

    monkeypatch.setattr(learning_service_module, "LearningService", _VerifiedService)
    event = StreamEvent(
        type=StreamEventType.CONTENT,
        metadata={
            "qa_pair": {
                "question_id": "q-1",
                "question": "Solve x + 2 = 4.",
                "correct_answer": "2",
                "subject_id": "math",
                "kc_id": "kc-linear",
                "learning_path_id": "model-invented-path",
            }
        },
    )

    await runtime._capture_and_project_quiz_event(execution, event)

    persisted = await store.get_server_quiz_item("session-1", str(turn["id"]), "q-1")
    assert persisted is not None
    assert persisted["learning_path_id"] == "path-math"
    assert len(calls) == 1
