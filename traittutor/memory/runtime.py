"""Runtime composition for the owner-bound canonical memory store."""

from __future__ import annotations

from traittutor.multi_user.context import get_current_user

from .store import MemoryStore


def get_current_memory_store(owner_id: str | None = None) -> MemoryStore:
    """Return the owner-bound canonical memory store."""
    resolved_owner = owner_id or get_current_user().id
    store = MemoryStore(resolved_owner)
    return store


__all__ = ["get_current_memory_store"]
