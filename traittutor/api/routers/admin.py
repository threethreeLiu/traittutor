"""Consumer-safe administrator control plane."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import json
import sqlite3
from typing import Any, Literal

import anyio
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from traittutor.api.routers.auth import require_admin
from traittutor.multi_user.audit import log_admin_action
from traittutor.multi_user.context import get_current_user
from traittutor.multi_user.identity import (
    get_user_by_id,
    invalidate_sessions,
    list_user_info,
    set_disabled,
    set_role,
)
from traittutor.multi_user.paths import SYSTEM_ROOT, USERS_ROOT
from traittutor.services.config.model_catalog import get_model_catalog_service

router = APIRouter(dependencies=[Depends(require_admin)])

_SENSITIVE_KEY_MARKERS = ("api_key", "token", "password", "secret")

# Payload cap for overview/generation listings. Totals stay exact via
# COUNT(*); only the rendered rows are bounded.
_ADMIN_OVERVIEW_GENERATION_CAP = 500


class AdminUserAction(BaseModel):
    action: Literal["set_role", "set_disabled", "invalidate_sessions"]
    role: Literal["admin", "user"] | None = None
    disabled: bool | None = None


def _redact_catalog(value: Any, key: str = "") -> Any:
    if isinstance(value, dict):
        return {
            item_key: _redact_catalog(item_value, item_key)
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [_redact_catalog(item, key) for item in value]
    if key.lower() and any(marker in key.lower() for marker in _SENSITIVE_KEY_MARKERS):
        raw = str(value or "")
        return "" if not raw else ("****" if len(raw) < 9 else f"{raw[:4]}...{raw[-4:]}")
    return value


def _audit_events(limit: int = 100) -> list[dict[str, Any]]:
    path = SYSTEM_ROOT / "audit" / "usage.jsonl"
    if not path.exists():
        return []
    # Bounded memory: keep only the newest ``limit`` lines instead of
    # materialising the whole log as one string.
    tail: deque[str] = deque(maxlen=limit)
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            tail.append(line)
    events: list[dict[str, Any]] = []
    for line in tail:
        try:
            entry = json.loads(line)
            if isinstance(entry, dict):
                events.append(entry)
        except json.JSONDecodeError:
            continue
    return list(reversed(events))


def _generation_totals() -> int:
    total = 0
    for workspace in USERS_ROOT.glob("*/workspace"):
        database = workspace / "traittutor" / "traittutor.sqlite3"
        if not database.is_file():
            continue
        try:
            connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
            try:
                total += int(
                    connection.execute("SELECT COUNT(*) FROM generation_results").fetchone()[0]
                )
            finally:
                connection.close()
        except sqlite3.Error:
            continue
    return total


def _generation_summaries(limit: int | None = 100) -> list[dict[str, Any]]:
    """Newest-first generation summaries across every user workspace.

    Bounded by design: each workspace contributes at most ``cap`` rows via a
    SQL LIMIT (never a full-table payload load), and the merged result is
    sliced to ``cap``. The overview renders the top handful; scanning every
    generation ever recorded would pin the event loop as workspaces grow.
    """
    cap = limit if limit is not None else _ADMIN_OVERVIEW_GENERATION_CAP
    results: list[dict[str, Any]] = []
    for workspace in USERS_ROOT.glob("*/workspace"):
        database = workspace / "traittutor" / "traittutor.sqlite3"
        if not database.is_file():
            continue
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
            rows = connection.execute(
                "SELECT payload_json FROM generation_results ORDER BY rowid DESC LIMIT ?",
                (cap,),
            ).fetchall()
        except sqlite3.Error:
            continue
        finally:
            if connection is not None:
                connection.close()
        for (payload_json,) in rows:
            try:
                data = json.loads(payload_json)
                results.append(
                    {
                        "generation_id": data.get("generation_id"),
                        "generation_type": data.get("generation_type"),
                        "created_at": data.get("created_at"),
                        "status": data.get("status"),
                        "execution_mode": (data.get("result") or {}).get("execution_mode"),
                        "evaluation": (data.get("result") or {}).get("evaluation"),
                        "owner_user_id": workspace.parent.name,
                    }
                )
            except (TypeError, json.JSONDecodeError):
                continue
    ordered = sorted(results, key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return ordered[:cap]


@router.get("/overview")
async def overview() -> dict[str, Any]:
    users = list_user_info()
    # Sync multi-database scans stay off the event loop.
    generations, total = await anyio.to_thread.run_sync(
        lambda: (
            _generation_summaries(limit=_ADMIN_OVERVIEW_GENERATION_CAP),
            _generation_totals(),
        )
    )
    return {
        "users": {
            "total": len(users),
            "disabled": sum(bool(user.get("disabled")) for user in users),
        },
        "generations": {"recent": generations[:20], "total": total},
        "model_configured": bool(
            (get_model_catalog_service().load().get("services") or {})
            .get("llm", {})
            .get("profiles")
        ),
        "security_alerts": ["No administrator account configured"]
        if not any(user.get("role") == "admin" for user in users)
        else [],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/users")
async def users() -> dict[str, Any]:
    return {"users": list_user_info()}


@router.post("/users/{user_id}/action")
async def update_user(user_id: str, payload: AdminUserAction) -> dict[str, Any]:
    found = get_user_by_id(user_id)
    if found is None:
        raise HTTPException(status_code=404, detail="User not found")
    username, record = found
    actor = get_current_user()
    if user_id == actor.id and payload.action in {"set_disabled", "set_role"}:
        raise HTTPException(
            status_code=400, detail="Administrators cannot change their own access here"
        )
    if payload.action == "set_role":
        if payload.role is None or not set_role(username, payload.role):
            raise HTTPException(status_code=400, detail="Role update failed")
        before: dict[str, Any] = {"role": record.get("role")}
    elif payload.action == "set_disabled":
        if payload.disabled is None or not set_disabled(username, payload.disabled):
            raise HTTPException(status_code=400, detail="Account update failed")
        before = {"disabled": bool(record.get("disabled", False))}
    else:
        if not invalidate_sessions(username):
            raise HTTPException(status_code=400, detail="Session invalidation failed")
        before = {}
    log_admin_action(
        payload.action, target_user_id=user_id, summary={"username": username, "before": before}
    )
    return {"ok": True}


@router.get("/models")
async def models() -> dict[str, Any]:
    return {"catalog": _redact_catalog(get_model_catalog_service().load())}


@router.get("/generations")
async def generations(limit: int = 100) -> dict[str, Any]:
    return {"generations": _generation_summaries(max(1, min(limit, 500)))}


@router.get("/audit")
async def audit(limit: int = 100) -> dict[str, Any]:
    return {"events": _audit_events(max(1, min(limit, 500)))}
