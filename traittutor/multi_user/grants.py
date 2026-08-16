"""Logical resource grants for non-admin users."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import threading
from typing import Any

from traittutor.unified_storage import SectionedRecordStore

from .identity import get_user_by_id
from .paths import SYSTEM_ROOT, ensure_system_dirs

GRANTS_DIR = SYSTEM_ROOT / "grants"
_grant_lock = threading.RLock()


def _grant_store(user_id: str) -> SectionedRecordStore:
    return SectionedRecordStore(
        "user_grants",
        user_id,
        schema_version=1,
        db_path=SYSTEM_ROOT / "traittutor.sqlite3",
    )


def empty_grant(user_id: str) -> dict[str, Any]:
    return {
        "version": 2,
        "user_id": user_id,
        "models": {"llm": []},
        "knowledge_bases": [],
        "skills": [],
        # Tool whitelists gate optional built-ins for non-admin users:
        # ``enabled_tools=None`` means "default" (every tool in the pool),
        # ``[]`` means none, a list is an explicit whitelist. MCP tools can
        # proxy host-side capabilities, so non-admin runtime access treats
        # ``mcp_tools=None`` as deny-by-default until an admin grants explicit
        # names. ``exec_enabled`` is a tri-state override on top of the
        # deployment exec policy: ``None`` follows the policy, ``False`` always
        # denies, ``True`` is only honored where the sandbox can actually
        # isolate users (SYSTEM isolation).
        "enabled_tools": None,
        "mcp_tools": None,
        "exec_enabled": None,
    }


def _normalize_tool_list(value: Any) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        return None
    return [str(item).strip() for item in value if str(item).strip()]


def grant_path(user_id: str) -> Path:
    ensure_system_dirs()
    return GRANTS_DIR / f"{user_id}.json"


def normalize_grant(user_id: str, payload: dict[str, Any] | None) -> dict[str, Any]:
    """Coerce any stored/submitted grant payload into the v2 shape.

    v1 grants normalize losslessly for everything that was ever enforced:
    ``models.embedding`` / ``models.search`` / ``spaces`` had no runtime
    consumers and are dropped; absent v2 fields default to unrestricted.
    """
    base = empty_grant(user_id)
    if not isinstance(payload, dict):
        return base
    base["user_id"] = user_id
    models_value = payload.get("models")
    models = models_value if isinstance(models_value, dict) else {}
    items = models.get("llm") if isinstance(models, dict) else []
    if not isinstance(items, list):
        items = []
    base["models"]["llm"] = [dict(item) for item in items if isinstance(item, dict)]
    for key in ("knowledge_bases", "skills"):
        raw_values = payload.get(key)
        values = raw_values if isinstance(raw_values, list) else []
        base[key] = [dict(item) for item in values if isinstance(item, dict)]
    for key in ("enabled_tools", "mcp_tools"):
        base[key] = _normalize_tool_list(payload.get(key))
    exec_enabled = payload.get("exec_enabled")
    base["exec_enabled"] = bool(exec_enabled) if isinstance(exec_enabled, bool) else None
    return base


def load_grant(user_id: str) -> dict[str, Any]:
    record = next(
        (
            item
            for item in _grant_store(user_id).snapshot()["grants"]
            if item.get("user_id") == user_id
        ),
        None,
    )
    return normalize_grant(user_id, record)


def save_grant(user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    user_record = get_user_by_id(user_id)
    if user_record is None:
        raise ValueError(f"Unknown user id: {user_id}")
    _username, record = user_record
    if str(record.get("role") or "user") == "admin":
        raise ValueError("Admin users use the main workspace and cannot receive assignments.")
    grant = normalize_grant(user_id, payload)
    validate_grant(grant)
    with _grant_lock:
        adapter = _grant_store(user_id)
        with adapter.locked() as state:
            state["grants"] = [item for item in state["grants"] if item.get("user_id") != user_id]
            state["grants"].append(grant)
            adapter.replace_all(state)
    return grant


def validate_grant(grant: dict[str, Any]) -> None:
    """Reject accidental secret/path material in grants.

    Grants carry logical ids only. Runtime resolution happens server-side.
    """
    forbidden = {"api_key", "secret", "password", "token", "path", "base_url"}

    def walk(value: Any, trail: str = "grant") -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                lowered = str(key).lower()
                if lowered in forbidden or lowered.endswith("_key"):
                    raise ValueError(f"Grants must not contain secret/path field: {trail}.{key}")
                walk(child, f"{trail}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, f"{trail}[{index}]")

    walk(grant)


def public_grant(user_id: str) -> dict[str, Any]:
    return deepcopy(load_grant(user_id))
