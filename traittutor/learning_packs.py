"""Small, user-workspace learning-pack store for the consumer study tools."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from traittutor.services.path_service import get_path_service


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _path() -> Path:
    return get_path_service().get_workspace_dir() / "traittutor" / "learning-packs.json"


def _load() -> list[dict[str, Any]]:
    path = _path()
    if not path.exists():
        return []
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return [_normalize_pack(item) for item in value if isinstance(item, dict)] if isinstance(value, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _save(packs: list[dict[str, Any]]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(packs, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _normalize_pack(pack: dict[str, Any]) -> dict[str, Any]:
    """Additive migration for packs created before component orchestration."""
    normalized = dict(pack)
    normalized.setdefault("component_plans", [])
    normalized.setdefault("active_plan_id", None)
    normalized.setdefault("component_progress", {})
    normalized.setdefault("sources", [])
    normalized.setdefault("artifacts", {"courseware": [], "flashcards": [], "quiz": []})
    normalized.setdefault("flashcard_progress", {})
    normalized.setdefault("quiz_attempts", [])
    return normalized


def list_packs() -> list[dict[str, Any]]:
    return sorted(_load(), key=lambda item: str(item.get("updated_at", "")), reverse=True)


def get_pack(pack_id: str) -> dict[str, Any] | None:
    return next((item for item in _load() if item.get("pack_id") == pack_id), None)


def _normalize_goal(goal: dict[str, Any] | str | None) -> dict[str, Any] | None:
    if isinstance(goal, str):
        text = goal.strip()
        payload: dict[str, Any] = {"text": text}
    elif isinstance(goal, dict):
        text = str(goal.get("text") or goal.get("title") or "").strip()
        payload = dict(goal)
        payload["text"] = text
    else:
        return None
    if not text:
        return None
    payload.setdefault("goal_id", uuid4().hex)
    payload.setdefault("status", "active")
    payload.setdefault("created_at", _now())
    return payload


def _initial_sources(material: dict[str, Any], sources: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    normalized = [dict(item) for item in (sources or []) if isinstance(item, dict)]
    if material and not normalized:
        normalized.append({
            "source_type": str(material.get("source_type") or "paste"),
            "source_id": material.get("source_id"),
            "title": str(material.get("title") or "Learning source"),
            "role": str((material.get("metadata") or {}).get("source_kind") or "material")
            if isinstance(material.get("metadata"), dict)
            else "material",
        })
    return normalized


def create_pack(
    *,
    title: str,
    material: dict[str, Any] | None = None,
    profile_id: str | None = None,
    goal: dict[str, Any] | str | None = None,
    sources: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    material_payload = dict(material or {})
    pack = {
        "pack_id": uuid4().hex,
        "title": title.strip() or "未命名学习包",
        "goal": _normalize_goal(goal),
        "sources": _initial_sources(material_payload, sources),
        # ``material`` remains for backward compatibility with the generation
        # suite. New packs are goal-centered and may start without an uploaded
        # document; material is one optional source, not the pack identity.
        "material": material_payload,
        "profile_id": profile_id,
        "persona": None,
        "artifacts": {"courseware": [], "flashcards": [], "quiz": []},
        "flashcard_progress": {},
        "quiz_attempts": [],
        "component_plans": [],
        "active_plan_id": None,
        "component_progress": {},
        "created_at": _now(),
        "updated_at": _now(),
    }
    packs = _load()
    packs.append(pack)
    _save(packs)
    return pack


def update_pack(pack_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
    packs = _load()
    for pack in packs:
        if pack.get("pack_id") != pack_id:
            continue
        for key in ("title", "persona", "profile_id", "material"):
            if key in patch:
                pack[key] = patch[key]
        if "goal" in patch:
            pack["goal"] = _normalize_goal(patch["goal"])
        if "sources" in patch and isinstance(patch["sources"], list):
            pack["sources"] = [dict(item) for item in patch["sources"] if isinstance(item, dict)]
        if "source" in patch and isinstance(patch["source"], dict):
            pack.setdefault("sources", []).append(dict(patch["source"]))
        if "artifact" in patch and isinstance(patch["artifact"], dict):
            artifact = patch["artifact"]
            kind = str(artifact.get("kind") or "")
            if kind in pack["artifacts"]:
                pack["artifacts"][kind].append(artifact)
        if "flashcard_progress" in patch and isinstance(patch["flashcard_progress"], dict):
            pack["flashcard_progress"].update(patch["flashcard_progress"])
        if "quiz_attempt" in patch and isinstance(patch["quiz_attempt"], dict):
            pack["quiz_attempts"].append(patch["quiz_attempt"])
        if "active_plan_id" in patch:
            pack["active_plan_id"] = patch["active_plan_id"]
        if "component_progress" in patch and isinstance(patch["component_progress"], dict):
            pack["component_progress"].update(patch["component_progress"])
        pack["updated_at"] = _now()
        _save(packs)
        return pack
    return None


def list_component_plans(pack_id: str) -> list[dict[str, Any]]:
    pack = get_pack(pack_id)
    if pack is None:
        return []
    return list(pack.get("component_plans") or [])


def get_component_plan(pack_id: str, plan_id: str) -> dict[str, Any] | None:
    return next(
        (item for item in list_component_plans(pack_id) if item.get("plan_id") == plan_id),
        None,
    )


def create_component_plan(pack_id: str, plan: dict[str, Any]) -> dict[str, Any] | None:
    """Persist one immutable plan version and make it active.

    Earlier plans remain available for audit and reconnect. Replanning marks
    only the previous active version as superseded; completed component output
    is copied by the selector rather than mutated here.
    """
    packs = _load()
    for pack in packs:
        if pack.get("pack_id") != pack_id:
            continue
        plans = pack.setdefault("component_plans", [])
        plan_id = str(plan.get("plan_id") or "")
        if not plan_id or any(item.get("plan_id") == plan_id for item in plans):
            return None
        previous_id = pack.get("active_plan_id")
        if previous_id:
            for previous in plans:
                if previous.get("plan_id") == previous_id and previous.get("status") == "active":
                    previous["status"] = "superseded"
                    previous["updated_at"] = _now()
        payload = dict(plan)
        plans.append(payload)
        pack["active_plan_id"] = plan_id
        pack["component_progress"].setdefault(plan_id, {"events": [], "updated_at": _now()})
        pack["updated_at"] = _now()
        _save(packs)
        return payload
    return None


def record_component_event(
    pack_id: str,
    plan_id: str,
    component_id: str,
    event: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Append an idempotent interaction and update component progress."""
    packs = _load()
    for pack in packs:
        if pack.get("pack_id") != pack_id:
            continue
        plan = next((item for item in pack.get("component_plans", []) if item.get("plan_id") == plan_id), None)
        if plan is None:
            return None
        component = next((item for item in plan.get("components", []) if item.get("component_id") == component_id), None)
        if component is None:
            return None
        progress = pack.setdefault("component_progress", {}).setdefault(plan_id, {"events": []})
        events = progress.setdefault("events", [])
        event_id = str(event.get("event_id") or "")
        if event_id and any(item.get("event_id") == event_id for item in events):
            return pack, component
        payload = {**event, "occurred_at": str(event.get("occurred_at") or _now())}
        events.append(payload)
        action = str(payload.get("action") or "")
        if action == "start" and component.get("status") == "pending":
            component["status"] = "active"
        elif action == "complete":
            component["status"] = "completed"
        elif action == "skip" and not component.get("required", True):
            component["status"] = "skipped"
        elif action == "degrade":
            component["status"] = "degraded"
        elif action == "retry":
            component["status"] = "active"
        if payload.get("output_ref"):
            component["output_ref"] = str(payload["output_ref"])
        timestamp = _now()
        progress[component_id] = {
            "status": component.get("status"),
            "last_action": action,
            "updated_at": timestamp,
            "output_ref": component.get("output_ref"),
        }
        progress["updated_at"] = timestamp
        plan["updated_at"] = timestamp
        required = [item for item in plan.get("components", []) if item.get("required", True)]
        if required and all(item.get("status") == "completed" for item in required):
            plan["status"] = "completed"
        pack["updated_at"] = timestamp
        _save(packs)
        return pack, component
    return None
