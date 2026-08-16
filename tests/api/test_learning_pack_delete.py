from __future__ import annotations

from pathlib import Path

import pytest

from traittutor import learning_packs
from traittutor.api.routers.learning_packs import (
    DeletePacksRequest,
    delete_learning_pack,
    delete_learning_packs,
)
from traittutor.services.path_service import PathService
from traittutor.services.session import sqlite_store as session_sqlite_store


@pytest.fixture
def learning_workspace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> PathService:
    service = PathService(workspace_root=tmp_path)
    monkeypatch.setattr(learning_packs, "get_path_service", lambda: service)
    # The session backend resolves its database through the module-level path
    # service and caches one store per database path. Point it at the same
    # temporary workspace and drop the cache so tests never touch real data.
    monkeypatch.setattr(
        session_sqlite_store,
        "get_path_service",
        lambda: service,
    )
    session_sqlite_store._instances.clear()
    return service


def test_delete_packs_removes_only_requested_records(learning_workspace: PathService) -> None:
    first = learning_packs.create_pack(title="First")
    second = learning_packs.create_pack(title="Second")
    third = learning_packs.create_pack(title="Third")

    removed = learning_packs.delete_packs([second["pack_id"], first["pack_id"], second["pack_id"]])

    assert [pack["pack_id"] for pack in removed] == [second["pack_id"], first["pack_id"]]
    assert [pack["pack_id"] for pack in learning_packs.list_packs()] == [third["pack_id"]]


@pytest.mark.asyncio
async def test_batch_delete_reports_deleted_and_missing_ids(
    learning_workspace: PathService,
) -> None:
    first = learning_packs.create_pack(title="First")
    second = learning_packs.create_pack(title="Second")

    response = await delete_learning_packs(
        DeletePacksRequest(pack_ids=[first["pack_id"], "missing", first["pack_id"]])
    )

    assert response == {
        "deleted_ids": [first["pack_id"]],
        "missing_ids": ["missing"],
        "deleted_count": 1,
    }
    assert learning_packs.get_pack(second["pack_id"]) is not None


@pytest.mark.asyncio
async def test_single_delete_returns_not_found_after_removal(
    learning_workspace: PathService,
) -> None:
    pack = learning_packs.create_pack(title="Only")

    assert await delete_learning_pack(pack["pack_id"]) == {"deleted_id": pack["pack_id"]}
    with pytest.raises(Exception) as exc_info:
        await delete_learning_pack(pack["pack_id"])
    assert getattr(exc_info.value, "status_code", None) == 404


@pytest.mark.asyncio
async def test_batch_delete_removes_orphaned_learn_session(
    learning_workspace: PathService,
) -> None:
    from traittutor.services.session import get_sqlite_session_store

    store = get_sqlite_session_store()
    session_id = "learn-session-orphan"
    await store.create_session(session_id=session_id, title="Learn upload")
    pack = learning_packs.create_pack(
        title="With session",
        material={"metadata": {"learning_session_id": session_id}},
    )

    response = await delete_learning_packs(DeletePacksRequest(pack_ids=[pack["pack_id"]]))

    assert response["deleted_ids"] == [pack["pack_id"]]
    assert await store.get_session(session_id) is None


@pytest.mark.asyncio
async def test_batch_delete_keeps_session_still_used_by_another_pack(
    learning_workspace: PathService,
) -> None:
    from traittutor.services.session import get_sqlite_session_store

    store = get_sqlite_session_store()
    session_id = "learn-session-shared"
    await store.create_session(session_id=session_id, title="Learn upload")
    removed = learning_packs.create_pack(
        title="Removed",
        material={"metadata": {"learning_session_id": session_id}},
    )
    survivor = learning_packs.create_pack(
        title="Survivor",
        material={"metadata": {"learning_session_id": session_id}},
    )

    response = await delete_learning_packs(DeletePacksRequest(pack_ids=[removed["pack_id"]]))

    assert response["deleted_ids"] == [removed["pack_id"]]
    assert learning_packs.get_pack(survivor["pack_id"]) is not None
    assert await store.get_session(session_id) is not None


@pytest.mark.asyncio
async def test_single_delete_removes_orphaned_learn_session(
    learning_workspace: PathService,
) -> None:
    from traittutor.services.session import get_sqlite_session_store

    store = get_sqlite_session_store()
    session_id = "learn-session-single"
    await store.create_session(session_id=session_id, title="Learn upload")
    pack = learning_packs.create_pack(
        title="With session",
        material={"metadata": {"learning_session_id": session_id}},
    )

    assert await delete_learning_pack(pack["pack_id"]) == {"deleted_id": pack["pack_id"]}
    assert await store.get_session(session_id) is None


@pytest.mark.asyncio
async def test_delete_without_session_link_leaves_sessions_alone(
    learning_workspace: PathService,
) -> None:
    from traittutor.services.session import get_sqlite_session_store

    store = get_sqlite_session_store()
    unrelated = "learn-session-unrelated"
    await store.create_session(session_id=unrelated, title="Unrelated")
    pack = learning_packs.create_pack(title="No link")

    response = await delete_learning_packs(DeletePacksRequest(pack_ids=[pack["pack_id"]]))

    assert response["deleted_ids"] == [pack["pack_id"]]
    assert await store.get_session(unrelated) is not None
