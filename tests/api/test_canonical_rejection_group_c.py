from __future__ import annotations

from fastapi import HTTPException
import pytest

from traittutor.api.routers import knowledge as knowledge_router
from traittutor.api.routers import sessions as sessions_router
from traittutor.api.routers import traittutor_profile as profile_router
from traittutor.knowledge.manager import KnowledgeBaseManager
from traittutor.services.config.runtime_settings import RuntimeSettingsService
from traittutor.services.session.sqlite_store import SQLiteSessionStore


def test_retired_quizviewer_session_routes_are_not_registered() -> None:
    paths = {route.path for route in sessions_router.router.routes}

    assert "/{session_id}/quiz-results" not in paths
    assert "/{session_id}/quiz/grade" not in paths


def test_unknown_knowledge_provider_fails_instead_of_defaulting() -> None:
    with pytest.raises(HTTPException) as exc_info:
        knowledge_router._validate_registered_provider("retired-provider")

    assert exc_info.value.status_code == 422


def test_rag_storage_directory_is_not_auto_registered(tmp_path) -> None:
    base_dir = tmp_path / "knowledge"
    (base_dir / "old-kb" / "rag_storage").mkdir(parents=True)

    manager = KnowledgeBaseManager(base_dir=base_dir)

    assert manager.list_knowledge_bases() == []


def test_flat_document_parsing_config_is_rejected(tmp_path) -> None:
    settings_dir = tmp_path / "settings"
    settings_dir.mkdir()
    service = RuntimeSettingsService(settings_dir, process_env={})
    service._documents.save(  # noqa: SLF001 - inject malformed persisted contract
        "document_parsing",
        {"version": 1, "mode": "cloud", "api_token": "old"},
    )

    with pytest.raises(ValueError, match="Unsupported keys|version"):
        service.load_document_parsing(include_process_overrides=False)


@pytest.mark.asyncio
async def test_profile_list_returns_canonical_record_without_dynamic_upgrade(monkeypatch) -> None:
    stored = {
        "profile_id": "profile-1",
        "scores": {},
        "metadata": {"slr_support": {"source": "big_five_initial"}},
    }
    monkeypatch.setattr(profile_router, "list_trait_profiles", lambda: [stored])

    result = await profile_router.list_profiles()

    assert result["profiles"] == [stored]


@pytest.mark.asyncio
async def test_sqlite_message_write_does_not_auto_chain(tmp_path) -> None:
    store = SQLiteSessionStore(tmp_path / "sessions.sqlite")
    await store.create_session(session_id="session-1")
    await store.add_message("session-1", "user", "first")
    await store.add_message("session-1", "user", "second")

    messages = await store.get_messages("session-1")

    assert [message["parent_message_id"] for message in messages] == [None, None]
