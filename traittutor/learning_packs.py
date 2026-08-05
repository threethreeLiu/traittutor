"""Small, user-workspace learning-pack store for the consumer study tools."""

from __future__ import annotations

from datetime import UTC, datetime
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from traittutor.services.path_service import get_path_service


class LearningPackStoreError(RuntimeError):
    """The durable learning-pack store cannot safely serve a request."""


class InvalidComponentTransition(ValueError):
    """A component event violates the active plan's state machine."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _path() -> Path:
    return get_path_service().get_workspace_dir() / "traittutor" / "learning-packs.json"


def _lock_path() -> Path:
    return _path().with_suffix(".lock")


def _load() -> list[dict[str, Any]]:
    path = _path()
    if not path.exists():
        return []
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return [_normalize_pack(item) for item in value if isinstance(item, dict)] if isinstance(value, list) else []
    except OSError as exc:
        raise LearningPackStoreError("Unable to read learning packs") from exc
    except json.JSONDecodeError as exc:
        # Returning an empty list here used to make a damaged store look like
        # every learner had lost their work.  Fail visibly and preserve the
        # file for recovery instead.
        raise LearningPackStoreError("Learning-pack data is corrupted") from exc


def _save(packs: list[dict[str, Any]]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(packs, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


@contextmanager
def _locked_packs():
    """Serialize read-modify-write updates across web workers and processes."""
    lock = _lock_path()
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield _load()
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


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
    with _locked_packs() as packs:
        packs.append(pack)
        _save(packs)
    return pack


def update_pack(pack_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
    with _locked_packs() as packs:
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
    with _locked_packs() as packs:
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
    with _locked_packs() as packs:
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
            if pack.get("active_plan_id") != plan_id or plan.get("status") != "active":
                raise InvalidComponentTransition("Events can only update the active learning plan")
            payload = {**event, "occurred_at": str(event.get("occurred_at") or _now())}
            action = str(payload.get("action") or "")
            dependencies = set(component.get("dependencies") or [])
            completed = {
                str(item.get("component_id"))
                for item in plan.get("components", [])
                if item.get("status") in {"completed", "skipped"}
            }
            if action in {"start", "complete", "feedback"} and not dependencies.issubset(completed):
                raise InvalidComponentTransition("Complete prerequisite components before this step")
            current_status = str(component.get("status") or "pending")
            if action == "start" and current_status == "pending":
                component["status"] = "active"
            elif action == "complete" and current_status in {"pending", "active", "degraded"}:
                component["status"] = "completed"
            elif action == "skip" and not component.get("required", True) and current_status in {"pending", "active", "degraded"}:
                component["status"] = "skipped"
            elif action == "degrade" and current_status in {"pending", "active"}:
                component["status"] = "degraded"
            elif action == "retry" and current_status == "degraded":
                component["status"] = "active"
            elif action == "feedback" and current_status == "active":
                pass
            else:
                raise InvalidComponentTransition(f"Cannot {action} a {current_status} component")
            events.append(payload)
            if payload.get("output_ref"):
                component["output_ref"] = str(payload["output_ref"])
            if payload.get("media_url"):
                component["media_url"] = str(payload["media_url"])
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
