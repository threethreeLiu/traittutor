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
        return value if isinstance(value, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _save(packs: list[dict[str, Any]]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(packs, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def list_packs() -> list[dict[str, Any]]:
    return sorted(_load(), key=lambda item: str(item.get("updated_at", "")), reverse=True)


def get_pack(pack_id: str) -> dict[str, Any] | None:
    return next((item for item in _load() if item.get("pack_id") == pack_id), None)


def create_pack(*, title: str, material: dict[str, Any], profile_id: str | None = None) -> dict[str, Any]:
    pack = {
        "pack_id": uuid4().hex,
        "title": title.strip() or "未命名学习包",
        "material": material,
        "profile_id": profile_id,
        "persona": None,
        "artifacts": {"courseware": [], "flashcards": [], "quiz": []},
        "flashcard_progress": {},
        "quiz_attempts": [],
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
        if "artifact" in patch and isinstance(patch["artifact"], dict):
            artifact = patch["artifact"]
            kind = str(artifact.get("kind") or "")
            if kind in pack["artifacts"]:
                pack["artifacts"][kind].append(artifact)
        if "flashcard_progress" in patch and isinstance(patch["flashcard_progress"], dict):
            pack["flashcard_progress"].update(patch["flashcard_progress"])
        if "quiz_attempt" in patch and isinstance(patch["quiz_attempt"], dict):
            pack["quiz_attempts"].append(patch["quiz_attempt"])
        pack["updated_at"] = _now()
        _save(packs)
        return pack
    return None
