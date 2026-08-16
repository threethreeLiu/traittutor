"""Deleting a Learn session from Recents removes the Packs that reference it.

The sidebar Recents and the learning map (My learning) share a soft link:
``pack.materials[0].metadata.learning_session_id``. Pack deletion already
cleans up orphaned Learn sessions; this suite covers the opposite direction —
deleting the session must remove every referencing Pack so both lists stay
consistent, while unrelated sessions and Assist conversations stay untouched.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from traittutor import learning_packs
from traittutor.api.routers import sessions as sessions_router
from traittutor.services.path_service import PathService
from traittutor.services.session import sqlite_store as session_sqlite_store


@pytest.fixture
def learning_workspace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> PathService:
    service = PathService(workspace_root=tmp_path)
    monkeypatch.setattr(learning_packs, "get_path_service", lambda: service)
    monkeypatch.setattr(
        session_sqlite_store,
        "get_path_service",
        lambda: service,
    )
    session_sqlite_store._instances.clear()
    return service


async def _make_session(session_id: str, *, mode: str = "learn") -> str:
    from traittutor.services.session import get_sqlite_session_store

    store = get_sqlite_session_store()
    await store.create_session(session_id=session_id, title=f"{mode} session {session_id}")
    if mode != "learn":
        await store.update_session_preferences(session_id, {"workspace_mode": mode})
    return session_id


@pytest.mark.asyncio
async def test_session_delete_removes_linked_learning_pack(
    learning_workspace: PathService,
) -> None:
    from traittutor.services.session import get_sqlite_session_store

    store = get_sqlite_session_store()
    session_id = await _make_session("learn-session-linked")
    pack = learning_packs.create_pack(
        title="Linked path",
        material={"metadata": {"learning_session_id": session_id}},
    )
    other = learning_packs.create_pack(title="Unrelated path")

    response = await sessions_router.delete_session(session_id)

    assert response["deleted"] is True
    assert response["deleted_pack_ids"] == [pack["pack_id"]]
    assert learning_packs.get_pack(pack["pack_id"]) is None
    assert learning_packs.get_pack(other["pack_id"]) is not None
    assert await store.get_session(session_id) is None


@pytest.mark.asyncio
async def test_session_delete_removes_all_packs_referencing_it(
    learning_workspace: PathService,
) -> None:
    session_id = await _make_session("learn-session-multi")
    first = learning_packs.create_pack(
        title="First",
        material={"metadata": {"learning_session_id": session_id}},
    )
    second = learning_packs.create_pack(
        title="Second",
        material={"metadata": {"learning_session_id": session_id}},
    )

    response = await sessions_router.delete_session(session_id)

    assert sorted(response["deleted_pack_ids"]) == sorted([first["pack_id"], second["pack_id"]])
    assert learning_packs.list_packs() == []


@pytest.mark.asyncio
async def test_assist_session_delete_never_touches_packs(
    learning_workspace: PathService,
) -> None:
    from traittutor.services.session import get_sqlite_session_store

    store = get_sqlite_session_store()
    session_id = await _make_session("assist-session", mode="assist")
    pack = learning_packs.create_pack(title="Keep me")

    response = await sessions_router.delete_session(session_id)

    assert response["deleted_pack_ids"] == []
    assert learning_packs.get_pack(pack["pack_id"]) is not None
    assert await store.get_session(session_id) is None


@pytest.mark.asyncio
async def test_learn_session_without_pack_deletes_cleanly(
    learning_workspace: PathService,
) -> None:
    session_id = await _make_session("learn-session-nolink")

    response = await sessions_router.delete_session(session_id)

    assert response["deleted"] is True
    assert response["deleted_pack_ids"] == []
    assert learning_packs.list_packs() == []
