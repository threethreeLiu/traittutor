import asyncio

from fastapi import HTTPException
import pytest

from traittutor.api.routers.learning_packs import get_learning_pack_for_session
from traittutor.generate.service import GenerationRequest, MaterialSource
from traittutor.generate.tasks import GenerationTaskManager
from traittutor.learning_packs import create_pack, list_packs
from traittutor.multi_user.context import reset_current_user, set_current_user
from traittutor.multi_user.models import CurrentUser
from traittutor.multi_user.paths import scope_for_user
from traittutor.services.path_service import PathService


def _user(user_id: str) -> CurrentUser:
    return CurrentUser(
        id=user_id,
        username=user_id,
        role="user",
        scope=scope_for_user(user_id, is_admin=False),
    )


def test_learning_packs_are_scoped_to_the_current_user(monkeypatch, tmp_path):
    """A pack written in one authenticated workspace is invisible to another."""
    services: dict[str, PathService] = {}

    def current_service() -> PathService:
        from traittutor.multi_user.context import get_current_user

        user = get_current_user()
        return services.setdefault(user.id, PathService(workspace_root=tmp_path / user.id))

    monkeypatch.setattr("traittutor.learning_packs.get_path_service", current_service)

    first = _user("learner-one")
    second = _user("learner-two")
    first_token = set_current_user(first)
    try:
        pack = create_pack(title="Private goal", goal="Learn algebra")
        assert [item["pack_id"] for item in list_packs()] == [pack["pack_id"]]
    finally:
        reset_current_user(first_token)

    second_token = set_current_user(second)
    try:
        assert list_packs() == []
    finally:
        reset_current_user(second_token)


def test_generation_task_captures_current_user_for_async_execution(monkeypatch, tmp_path):
    """The worker restores the creator's identity before doing workspace I/O."""
    manager = GenerationTaskManager(storage_root=tmp_path)
    monkeypatch.setattr(manager, "_schedule", lambda: None)
    token = set_current_user(_user("generation-owner"))
    try:
        task = manager.create(
            GenerationRequest(
                generation_type="quiz",
                material=MaterialSource(source_type="paste", text="source", title="Source"),
            )
        )
    finally:
        reset_current_user(token)
    assert task.owner_id == "generation-owner"
    assert task.owner_username == "generation-owner"
    assert manager._store.load(task.generation_id).owner_id == "generation-owner"


def test_learning_session_link_is_exact_and_user_scoped(monkeypatch, tmp_path):
    """A reopened Learn session resolves its own Pack without scanning a page."""
    services: dict[str, PathService] = {}

    def current_service() -> PathService:
        from traittutor.multi_user.context import get_current_user

        user = get_current_user()
        return services.setdefault(user.id, PathService(workspace_root=tmp_path / user.id))

    monkeypatch.setattr("traittutor.learning_packs.get_path_service", current_service)
    first_token = set_current_user(_user("path-owner"))
    try:
        pack = create_pack(
            title="Linked path",
            goal="Learn algebra",
            material={"metadata": {"learning_session_id": "learn-session-1"}},
        )
        resolved = asyncio.run(get_learning_pack_for_session("learn-session-1"))
        assert resolved["pack_id"] == pack["pack_id"]
    finally:
        reset_current_user(first_token)

    other_token = set_current_user(_user("path-other"))
    try:
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(get_learning_pack_for_session("learn-session-1"))
        assert exc_info.value.status_code == 404
    finally:
        reset_current_user(other_token)
