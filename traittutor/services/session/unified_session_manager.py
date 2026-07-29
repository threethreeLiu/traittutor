"""
Unified Session Manager
=======================

Single session manager that handles all conversation types (chat, deep_solve,
deep_question, etc.) via a ``mode`` field in the session data.
"""

from __future__ import annotations

from typing import Any

from .base_session_manager import BaseSessionManager


class UnifiedSessionManager(BaseSessionManager):
    """
    Session manager that stores conversations for every mode in one file.

    Session data structure::

        {
            "session_id": "unified_<uuid>",
            "title": "...",
            "mode": "chat" | "deep_solve" | "deep_question" | ...,
            "enabled_tools": ["rag", "web_search"],
            "knowledge_bases": ["math-kb"],
            "messages": [ {role, content, ...}, ... ],
            "metadata": { ... },  # capability-specific extras
            "created_at": ...,
            "updated_at": ...,
        }
    """

    def __init__(self) -> None:
        super().__init__(module_name="unified")

    def _get_session_id_prefix(self) -> str:
        return "unified_"

    def _get_default_title(self) -> str:
        return "New conversation"

    def _create_session_data(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "mode": kwargs.get("mode", "chat"),
            "enabled_tools": kwargs.get("enabled_tools", []),
            "knowledge_bases": kwargs.get("knowledge_bases", []),
            "messages": [],
            "metadata": kwargs.get("metadata", {}),
        }

    def _get_session_summary(self, session: dict[str, Any]) -> dict[str, Any]:
        messages = session.get("messages", [])
        last_message = messages[-1].get("content", "")[:120] if messages else ""
        return {
            "session_id": session.get("session_id", ""),
            "title": session.get("title", ""),
            "mode": session.get("mode", "chat"),
            "enabled_tools": session.get("enabled_tools", []),
            "knowledge_bases": session.get("knowledge_bases", []),
            "message_count": len(messages),
            "last_message": last_message,
            "created_at": session.get("created_at"),
            "updated_at": session.get("updated_at"),
        }


_instance: UnifiedSessionManager | None = None


def get_unified_session_manager() -> UnifiedSessionManager:
    global _instance
    if _instance is None:
        _instance = UnifiedSessionManager()
    return _instance
