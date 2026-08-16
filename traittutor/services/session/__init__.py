"""
Session Management Module
=========================

Provides unified session management for all agent modules.

Usage:
    from traittutor.services.session import BaseSessionManager

    class MySessionManager(BaseSessionManager):
        def __init__(self):
            super().__init__("my_module")

        def _get_session_id_prefix(self) -> str:
            return "my_"

        def _get_default_title(self) -> str:
            return "New My Session"

        # ... implement other abstract methods
"""

from .base_session_manager import BaseSessionManager
from .protocol import SessionStoreProtocol
from .sqlite_store import (
    SQLiteSessionStore,
    get_sqlite_session_store,
    make_imported_session_id,
)
from .turn_runtime import TurnRuntimeManager, get_turn_runtime_manager


def get_session_store() -> SessionStoreProtocol:
    """Return the canonical SQLite session backend."""
    return get_sqlite_session_store()


__all__ = [
    "BaseSessionManager",
    "SessionStoreProtocol",
    "SQLiteSessionStore",
    "TurnRuntimeManager",
    "get_session_store",
    "get_sqlite_session_store",
    "get_turn_runtime_manager",
    "make_imported_session_id",
]
