from __future__ import annotations

import asyncio

from fastapi import HTTPException
from pydantic import ValidationError
import pytest

from traittutor.api.routers import sessions as sessions_router
from traittutor.api.routers.question_notebook import UpsertEntryRequest, _public_notebook_entry
from traittutor.api.routers.quiz_judge import _resolve_server_held_judge_item
from traittutor.core.stream import StreamEvent, StreamEventType
from traittutor.services.session.sqlite_store import SQLiteSessionStore
from traittutor.services.session.turn_runtime import TurnRuntimeManager, _TurnExecution


def _private_pair() -> dict[str, object]:
    return {
        "question_id": "q-private",
        "question": "What is 2 + 2?",
        "question_type": "choice",
        "options": {"A": "3", "B": "4"},
        "correct_answer": "B",
        "explanation": "Two plus two is four.",
    }


def test_submitted_notebook_projection_uses_reference_answer_name() -> None:
    public = _public_notebook_entry(
        {
            "id": 1,
            "user_answer": "A",
            "is_correct": False,
            "correct_answer": "B",
            "explanation": "Server explanation",
        }
    )
    assert public["reference_answer"] == "B"
    assert public["explanation"] == "Server explanation"
    assert "correct_answer" not in public


@pytest.mark.asyncio
async def test_stream_captures_private_quiz_and_projects_safe_payload(tmp_path) -> None:
    store = SQLiteSessionStore(tmp_path / "sessions.sqlite")
    await store.create_session(session_id="session-1")
    turn = await store.create_turn("session-1", capability="deep_question")
    runtime = TurnRuntimeManager(store=store)
    execution = _TurnExecution(
        turn_id=turn["id"],
        session_id="session-1",
        capability="deep_question",
        payload={},
    )
    event = StreamEvent(
        type=StreamEventType.CONTENT,
        content="raw markdown answer B and explanation",
        metadata={"call_kind": "quiz_question_emitted", "qa_pair": _private_pair()},
    )

    await runtime._capture_and_project_quiz_event(execution, event)

    assert event.content == ""
    public_pair = event.metadata["qa_pair"]
    assert set(public_pair) == {
        "question_id",
        "question",
        "question_type",
        "options",
    }
    assert "correct_answer" not in public_pair
    assert "explanation" not in public_pair
    stored = await store.get_server_quiz_item("session-1", turn["id"], "q-private")
    assert stored is not None
    assert {key: stored[key] for key in _private_pair()} == _private_pair()

    result_event = StreamEvent(
        type=StreamEventType.RESULT,
        metadata={
            "response": "Question and answer B",
            "summary": {
                "success": True,
                "templates": [{"reference_answer": "B"}],
                "results": [{"qa_pair": _private_pair(), "metadata": {}}],
            },
        },
    )
    await runtime._capture_and_project_quiz_event(execution, result_event)
    assert "response" not in result_event.metadata
    assert "templates" not in result_event.metadata["summary"]
    assert "correct_answer" not in result_event.metadata["summary"]["results"][0]["qa_pair"]


def test_forged_quiz_fields_are_rejected_and_server_verdict_replays(tmp_path, monkeypatch) -> None:
    store = SQLiteSessionStore(tmp_path / "sessions.sqlite")

    async def run() -> tuple[dict[str, object], dict[str, object], dict[str, object] | None]:
        await store.create_session(session_id="session-1")
        turn = await store.create_turn("session-1", capability="deep_question")
        await store.upsert_server_quiz_items("session-1", turn["id"], [_private_pair()])
        monkeypatch.setattr(sessions_router, "get_sqlite_session_store", lambda: store)

        payload = {
            "session_id": "session-1",
            "turn_id": turn["id"],
            "question_id": "q-private",
            "answer": "A",
            "attempt_id": "attempt-1",
            "correct_answer": "A",
            "is_correct": True,
            "question": "forged replacement question",
        }
        with pytest.raises(ValidationError):
            UpsertEntryRequest.model_validate(payload)
        request = UpsertEntryRequest.model_validate(
            {
                key: value
                for key, value in payload.items()
                if key not in {"correct_answer", "is_correct", "question"}
            }
        )
        first = await sessions_router._grade_server_held_quiz_item(
            session_id="session-1",
            turn_id=turn["id"],
            question_id=request.question_id,
            answer=request.answer,
            attempt_id=request.attempt_id,
        )
        replay = await sessions_router._grade_server_held_quiz_item(
            session_id="session-1",
            turn_id=turn["id"],
            question_id=request.question_id,
            answer=request.answer,
            attempt_id=request.attempt_id,
        )
        entry = await store.find_notebook_entry("session-1", "q-private", turn["id"])
        return first, replay, entry

    first, replay, entry = asyncio.run(run())
    assert first["correct"] is False
    assert replay["correct"] is False
    assert entry is not None
    assert entry["is_correct"] is False
    # The private record, not the forged request, remains the answer source.
    assert entry["correct_answer"] == "B"


@pytest.mark.asyncio
async def test_missing_server_held_quiz_item_fails_closed(tmp_path, monkeypatch) -> None:
    store = SQLiteSessionStore(tmp_path / "sessions.sqlite")
    await store.create_session(session_id="session-1")
    turn = await store.create_turn("session-1", capability="deep_question")
    monkeypatch.setattr(sessions_router, "get_sqlite_session_store", lambda: store)

    with pytest.raises(HTTPException, match="Server-held quiz question not found"):
        await sessions_router._grade_server_held_quiz_item(
            session_id="session-1",
            turn_id=turn["id"],
            question_id="browser-invented",
            answer="anything",
            attempt_id="attempt-1",
        )


@pytest.mark.asyncio
async def test_judge_uses_server_item_not_forged_browser_answer_key() -> None:
    class _Store:
        async def get_server_quiz_item(self, *_args: object) -> dict[str, object]:
            return _private_pair()

    resolved = await _resolve_server_held_judge_item(
        {
            "session_id": "session-1",
            "turn_id": "turn-1",
            "question_id": "q-private",
            "question": "browser replacement",
            "correct_answer": "A",
            "explanation": "browser replacement explanation",
            "is_correct": True,
        },
        store=_Store(),
    )

    assert resolved is not None
    _session_id, _turn_id, _question_id, item = resolved
    assert item["correct_answer"] == "B"
    assert item["question"] == "What is 2 + 2?"
