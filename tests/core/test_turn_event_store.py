from __future__ import annotations

import pytest

from traittutor.services.session.sqlite_store import SQLiteSessionStore


@pytest.mark.asyncio
async def test_sqlite_store_persists_turns_and_events(tmp_path) -> None:
    store = SQLiteSessionStore(tmp_path / "chat_history.db")

    session = await store.create_session(title="Demo", session_id="session-demo")
    turn = await store.create_turn(session["id"], capability="chat")

    event_one = await store.append_turn_event(
        turn["id"],
        {
            "type": "session",
            "source": "test",
            "stage": "",
            "content": "",
            "metadata": {"session_id": session["id"]},
            "timestamp": 1.0,
        },
    )
    event_two = await store.append_turn_event(
        turn["id"],
        {
            "type": "content",
            "source": "chat",
            "stage": "responding",
            "content": "Hello Frank",
            "metadata": {"call_kind": "llm_final_response"},
            "timestamp": 2.0,
        },
    )

    assert event_one["seq"] == 1
    assert event_two["seq"] == 2

    active_turn = await store.get_active_turn(session["id"])
    assert active_turn is not None
    assert active_turn["id"] == turn["id"]
    assert active_turn["last_seq"] == 2

    replay = await store.get_turn_events(turn["id"], after_seq=1)
    assert [event["seq"] for event in replay] == [2]
    assert replay[0]["content"] == "Hello Frank"

    await store.update_turn_status(turn["id"], "completed")

    detail = await store.get_session_with_messages(session["id"])
    assert detail is not None
    assert detail["active_turns"] == []

    sessions = await store.list_sessions()
    assert sessions[0]["session_id"] == session["id"]
    assert sessions[0]["status"] == "completed"


@pytest.mark.asyncio
async def test_sqlite_store_persists_message_metadata(tmp_path) -> None:
    store = SQLiteSessionStore(tmp_path / "chat_history.db")
    session = await store.create_session(title="Demo", session_id="session-demo")

    await store.add_message(
        session_id=session["id"],
        role="user",
        content="hello",
        metadata={
            "request_snapshot": {
                "content": "hello",
                "skills": ["proof-checker"],
                "memoryReferences": ["summary"],
            }
        },
    )

    detail = await store.get_session_with_messages(session["id"])
    assert detail is not None
    assert detail["messages"][0]["metadata"]["request_snapshot"] == {
        "content": "hello",
        "skills": ["proof-checker"],
        "memoryReferences": ["summary"],
    }
