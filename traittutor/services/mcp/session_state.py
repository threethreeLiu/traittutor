"""
Per-session deferred-tool state.

Records which deferred tools the model has loaded (via ``load_tools``) in a
chat session, so subsequent turns include those schemas from the start
instead of forcing a re-load. File-backed JSON inside the session workspace
— multi-user-safe because the path service resolves per-user roots via the
runtime's ContextVars.
"""

from __future__ import annotations

import logging

from traittutor.multi_user.context import get_current_user
from traittutor.services.path_service import get_path_service
from traittutor.unified_storage import SectionedRecordStore

logger = logging.getLogger(__name__)

_STATE_FILENAME = "loaded_tools.json"


def _state_file(session_id: str):
    workspace = get_path_service().get_session_workspace("chat", session_id)
    return workspace / _STATE_FILENAME


def load_loaded_tools(session_id: str) -> set[str]:
    if not session_id:
        return set()
    try:
        record = next(
            (
                item
                for item in SectionedRecordStore(
                    "mcp_session_state",
                    get_current_user().id,
                    schema_version=1,
                    path_service=get_path_service(),
                ).snapshot()["sessions"]
                if item.get("session_id") == session_id
            ),
            None,
        )
    except Exception:
        logger.debug("loaded-tools state unreadable for %s", session_id, exc_info=True)
        return set()
    names = record.get("loaded_tools") if isinstance(record, dict) else None
    if not isinstance(names, list):
        return set()
    return {str(n) for n in names if str(n).strip()}


def record_loaded_tools(session_id: str, names: set[str]) -> None:
    if not session_id:
        return
    try:
        owner = get_current_user().id
        adapter = SectionedRecordStore(
            "mcp_session_state", owner, schema_version=1, path_service=get_path_service()
        )
        with adapter.locked() as payload:
            payload["sessions"] = [
                item for item in payload["sessions"] if item.get("session_id") != session_id
            ]
            payload["sessions"].append(
                {"session_id": session_id, "owner_id": owner, "loaded_tools": sorted(names)}
            )
            adapter.replace_all(payload)
    except Exception:
        logger.warning("failed to persist loaded-tools state for %s", session_id, exc_info=True)


__all__ = ["load_loaded_tools", "record_loaded_tools"]
