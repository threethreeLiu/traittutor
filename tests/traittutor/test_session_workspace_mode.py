import asyncio

from fastapi import HTTPException
import pytest

from traittutor.api.routers import sessions as session_router
from traittutor.api.routers.sessions import _with_workspace_mode


def test_explicit_workspace_mode_is_exposed_without_mutating_preferences():
    session = {"session_id": "s-1", "preferences": {"workspace_mode": "learn"}}

    enriched = _with_workspace_mode(session)

    assert enriched["mode"] == "learn"
    assert "mode" not in session
    assert enriched["preferences"] == {"workspace_mode": "learn"}


def test_missing_workspace_mode_is_rejected() -> None:
    with pytest.raises(HTTPException) as exc_info:
        _with_workspace_mode({"session_id": "old", "preferences": {}})

    assert exc_info.value.status_code == 409


def test_session_list_filters_explicit_workspace_modes(monkeypatch):
    class FakeStore:
        async def list_sessions(self, limit: int, offset: int):
            assert (limit, offset) == (200, 0)
            return [
                {"session_id": "learn-1", "preferences": {"workspace_mode": "learn"}},
                {"session_id": "assistant-1", "preferences": {"workspace_mode": "assist"}},
            ]

    monkeypatch.setattr(session_router, "get_session_store", lambda: FakeStore())

    learn = asyncio.run(session_router.list_sessions(limit=50, offset=0, mode="learn"))
    assist = asyncio.run(session_router.list_sessions(limit=50, offset=0, mode="assist"))

    assert [item["session_id"] for item in learn["sessions"]] == ["learn-1"]
    assert [item["session_id"] for item in assist["sessions"]] == ["assistant-1"]
