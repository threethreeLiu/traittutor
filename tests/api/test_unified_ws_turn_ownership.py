"""Authenticated owner boundaries for the canonical WebSocket protocol."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import WebSocketDisconnect
import pytest

from traittutor.api.routers import unified_ws
from traittutor.multi_user.context import reset_current_user, set_current_user
from traittutor.multi_user.models import CurrentUser
from traittutor.multi_user.paths import scope_for_user
from traittutor.services.session.sqlite_store import SQLiteSessionStore
from traittutor.services.session.turn_runtime import TurnRuntimeManager


def _user(user_id: str) -> CurrentUser:
    return CurrentUser(
        id=user_id,
        username=user_id,
        role="user",
        scope=scope_for_user(user_id, is_admin=False),
    )


class _WebSocket:
    def __init__(self, messages: list[dict[str, Any]], *, pause_before_close: bool = False) -> None:
        self._messages = iter(messages)
        self._pause_before_close = pause_before_close
        self.sent: list[dict[str, Any]] = []
        self.accepted = False

    async def accept(self) -> None:
        self.accepted = True

    async def receive_text(self) -> str:
        try:
            return json.dumps(next(self._messages))
        except StopIteration:
            if self._pause_before_close:
                await asyncio.sleep(0.02)
            raise WebSocketDisconnect() from None

    async def send_text(self, payload: str) -> None:
        self.sent.append(json.loads(payload))


async def _seed_owner_turn(
    store: SQLiteSessionStore,
    owner: CurrentUser,
) -> tuple[str, str]:
    token = set_current_user(owner)
    try:
        session = await store.create_session(session_id="owner-session")
        turn = await store.create_turn(str(session["id"]), capability="chat")
        await store.append_turn_event(
            str(turn["id"]),
            {
                "type": "text",
                "content": "owner-only transcript",
                "session_id": session["id"],
            },
        )
        return str(session["id"]), str(turn["id"])
    finally:
        reset_current_user(token)


def _install_authenticated_socket(
    monkeypatch: pytest.MonkeyPatch,
    runtime: TurnRuntimeManager,
    user: CurrentUser,
) -> None:
    async def authenticate(_ws: Any):
        return set_current_user(user)

    monkeypatch.setattr("traittutor.api.routers.auth.ws_require_auth", authenticate)
    monkeypatch.setattr(
        "traittutor.api.routers.auth.ws_reset_auth",
        lambda token: reset_current_user(token),
    )
    monkeypatch.setattr(
        "traittutor.services.session.get_turn_runtime_manager",
        lambda: runtime,
    )


@pytest.mark.asyncio
async def test_unified_ws_rejects_foreign_structured_reply_before_side_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SQLiteSessionStore(tmp_path / "sessions.sqlite")
    owner = _user("owner-a")
    _session_id, turn_id = await _seed_owner_turn(store, owner)
    runtime = TurnRuntimeManager(store=store)
    # A live queue is deliberately present: its mere in-process existence
    # cannot authorize another authenticated account to submit a reply.
    runtime._reply_queues[turn_id] = asyncio.Queue()
    _install_authenticated_socket(monkeypatch, runtime, _user("attacker-b"))

    message: dict[str, Any] = {
        "type": "submit_user_reply",
        "turn_id": turn_id,
        "owner_id": owner.id,  # attacker-controlled and intentionally ignored
        "answers": [{"questionId": "q1", "text": "attacker reply"}],
    }
    websocket = _WebSocket([message])
    await unified_ws.unified_websocket(websocket)  # type: ignore[arg-type]

    assert websocket.accepted is True
    assert len(websocket.sent) == 1
    assert websocket.sent[0]["type"] == "error"
    assert websocket.sent[0]["data"]["content"] == "Turn not found or unavailable."
    assert runtime._reply_queues[turn_id].empty()
    owner_token = set_current_user(owner)
    try:
        assert (await store.get_turn(turn_id))["status"] == "running"  # type: ignore[index]
    finally:
        reset_current_user(owner_token)


@pytest.mark.asyncio
async def test_unified_ws_allows_owner_to_submit_structured_reply(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SQLiteSessionStore(tmp_path / "sessions.sqlite")
    owner = _user("owner-a")
    _session_id, turn_id = await _seed_owner_turn(store, owner)
    runtime = TurnRuntimeManager(store=store)
    replies: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    runtime._reply_queues[turn_id] = replies
    _install_authenticated_socket(monkeypatch, runtime, owner)

    websocket = _WebSocket(
        [
            {
                "type": "submit_user_reply",
                "turn_id": turn_id,
                "answers": [{"questionId": "q1", "text": "my answer"}],
            }
        ],
        pause_before_close=True,
    )
    await unified_ws.unified_websocket(websocket)  # type: ignore[arg-type]

    assert await replies.get() == {"answers": [{"questionId": "q1", "text": "my answer"}]}
    owner_token = set_current_user(owner)
    try:
        assert (await store.get_turn(turn_id))["status"] == "running"  # type: ignore[index]
    finally:
        reset_current_user(owner_token)


@pytest.mark.asyncio
async def test_start_turn_rejects_foreign_existing_session_before_any_write(tmp_path: Path) -> None:
    store = SQLiteSessionStore(tmp_path / "sessions.sqlite")
    owner = _user("owner-a")
    session_id, turn_id = await _seed_owner_turn(store, owner)
    runtime = TurnRuntimeManager(store=store)
    attacker_token = set_current_user(_user("attacker-b"))
    try:
        with pytest.raises(RuntimeError, match="Session not found or unavailable"):
            await runtime.start_turn(
                {
                    "session_id": session_id,
                    "capability": "chat",
                    "content": "try to continue another user's session",
                    "config": {"product_mode": "assist"},
                }
            )
    finally:
        reset_current_user(attacker_token)

    owner_token = set_current_user(owner)
    try:
        # The rejected start did not create a second turn or change the
        # original running turn, even though it supplied the valid session id.
        assert [item["id"] for item in await store.list_active_turns(session_id)] == [turn_id]
    finally:
        reset_current_user(owner_token)


@pytest.mark.asyncio
async def test_unified_ws_disconnect_cancels_turn_started_on_that_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = _user("owner-a")
    cancelled: list[str] = []

    class _Runtime:
        async def start_turn(self, _message: dict[str, Any]):
            return {"id": "session-1"}, {"id": "turn-1"}

        async def subscribe_turn(self, _turn_id: str, *, after_seq: int):
            del after_seq
            await asyncio.Future()
            yield {}  # pragma: no cover - keeps this an async generator

        async def cancel_turn(self, turn_id: str) -> bool:
            cancelled.append(turn_id)
            return True

    runtime = _Runtime()
    _install_authenticated_socket(monkeypatch, runtime, owner)

    websocket = _WebSocket(
        [{"type": "start_turn", "content": "hello", "capability": "chat"}],
        pause_before_close=True,
    )
    await unified_ws.unified_websocket(websocket)  # type: ignore[arg-type]

    assert cancelled == ["turn-1"]


@pytest.mark.asyncio
async def test_unified_ws_rejects_cancel_turn_as_noncanonical_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = TurnRuntimeManager(store=SQLiteSessionStore(tmp_path / "sessions.sqlite"))
    _install_authenticated_socket(monkeypatch, runtime, _user("owner-a"))

    websocket = _WebSocket([{"type": "cancel_turn", "turn_id": "turn-1"}])
    await unified_ws.unified_websocket(websocket)  # type: ignore[arg-type]

    assert websocket.sent[0]["type"] == "error"
    assert websocket.sent[0]["data"]["content"] == "Unknown type: cancel_turn"
