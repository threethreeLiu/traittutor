"""Consumer-safe administrator control plane."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Literal

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


class AdminUserAction(BaseModel):
    action: Literal["set_role", "set_disabled", "invalidate_sessions"]
    role: Literal["admin", "user"] | None = None
    disabled: bool | None = None


def _redact_catalog(value: Any, key: str = "") -> Any:
    if isinstance(value, dict):
        return {item_key: _redact_catalog(item_value, item_key) for item_key, item_value in value.items()}
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
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines()[-limit:]:
        try:
            entry = json.loads(line)
            if isinstance(entry, dict):
                events.append(entry)
        except json.JSONDecodeError:
            continue
    return list(reversed(events))


def _generation_summaries(limit: int | None = 100) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for workspace in USERS_ROOT.glob("*/workspace"):
        for file in (workspace / "traittutor" / "generations").glob("*.json") if (workspace / "traittutor" / "generations").exists() else []:
            try:
                data = json.loads(file.read_text(encoding="utf-8"))
                results.append({
                    "generation_id": data.get("generation_id"), "generation_type": data.get("generation_type"),
                    "created_at": data.get("created_at"), "status": data.get("status"),
                    "execution_mode": (data.get("result") or {}).get("execution_mode"),
                    "evaluation": (data.get("result") or {}).get("evaluation"),
                    "owner_user_id": workspace.parent.name,
                })
            except (OSError, json.JSONDecodeError):
                continue
    ordered = sorted(results, key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return ordered if limit is None else ordered[:limit]


@router.get("/overview")
async def overview() -> dict[str, Any]:
    users = list_user_info()
    generations = _generation_summaries(limit=None)
    return {
        "users": {"total": len(users), "disabled": sum(bool(user.get("disabled")) for user in users)},
        "generations": {"recent": generations[:20], "total": len(generations)},
        "model_configured": bool((get_model_catalog_service().load().get("services") or {}).get("llm", {}).get("profiles")),
        "security_alerts": ["No administrator account configured"] if not any(user.get("role") == "admin" for user in users) else [],
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
        raise HTTPException(status_code=400, detail="Administrators cannot change their own access here")
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
    log_admin_action(payload.action, target_user_id=user_id, summary={"username": username, "before": before})
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
