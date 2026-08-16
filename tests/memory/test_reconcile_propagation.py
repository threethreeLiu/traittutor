"""Canonical memory deletion must propagate to derived state (invariant check).

AGENTS.md invariant: deleted, deactivated or revoked information must not
enter later snapshots or index generations. These tests pin the reconcile
path that the canonical memory mutation endpoints run inline: a deleted
explicit preference disappears from derived signals AND from an already
frozen per-session memory snapshot.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from traittutor.memory import runtime as memory_runtime
from traittutor.memory.store import MemoryStore
from traittutor.personalization.service import PersonalizationService

PREFERENCE_TEXT = "prefer geometry examples"


def _seed_explicit_preference(store: MemoryStore) -> str:
    item = store.add_explicit(
        scope="global",
        key="preference:example_style",
        value=PREFERENCE_TEXT,
        source="test_seeding",
    )
    return item.memory_id


@pytest.fixture
def services(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store = MemoryStore("local-admin", path=tmp_path / "memory.json")
    monkeypatch.setattr(memory_runtime, "get_current_memory_store", lambda owner_id=None: store)
    service = PersonalizationService()
    monkeypatch.setattr(service, "_root", lambda: tmp_path / "learner")
    return store, service


def test_deleted_preference_leaves_signals_and_frozen_session_snapshot(
    services,
) -> None:
    store, service = services
    memory_id = _seed_explicit_preference(store)

    async def driver() -> None:
        first = await service.reconcile_memory()
        assert first["state"] == "completed"
        assert first["imported"] == 1

        context = service.build_context(purpose="chat", session_id="session-1")
        assert context.degraded is False
        assert context.memory_snapshot is not None
        assert PREFERENCE_TEXT in context.memory_snapshot.explicit_preferences
        # The session froze its snapshot at first use.
        assert "memory_snapshot" in service._sessions()["session-1"]

        store.delete(memory_id, source="test_deletion", operation_id="op-delete")

        second = await service.reconcile_memory()
        assert second["state"] == "completed"

        # The frozen frame was invalidated, not silently preserved.
        assert "memory_snapshot" not in service._sessions()["session-1"]

        followup = service.build_context(purpose="chat", session_id="session-1")
        assert followup.degraded is False
        assert followup.memory_snapshot is not None
        assert PREFERENCE_TEXT not in followup.memory_snapshot.explicit_preferences

    asyncio.run(driver())


def test_deactivated_preference_leaves_frozen_session_snapshot(services) -> None:
    store, service = services
    memory_id = _seed_explicit_preference(store)

    async def driver() -> None:
        assert (await service.reconcile_memory())["state"] == "completed"
        context = service.build_context(purpose="chat", session_id="session-2")
        assert PREFERENCE_TEXT in context.memory_snapshot.explicit_preferences

        store.deactivate(memory_id, source="test_deactivation", operation_id="op-deactivate")
        assert (await service.reconcile_memory())["state"] == "completed"

        followup = service.build_context(purpose="chat", session_id="session-2")
        assert PREFERENCE_TEXT not in followup.memory_snapshot.explicit_preferences

    asyncio.run(driver())
