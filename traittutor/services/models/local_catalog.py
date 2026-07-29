"""Code-defined LLM catalog loader.

Parses ``config/models.local.yaml`` (gitignored; the committed contract lives in
``config/models.local.example.yaml``) into the ``services.llm`` sub-shape used by
``ModelCatalogService``. Each YAML entry is one route (its own base_url + api_key
+ model), so it becomes one profile containing a single model.

Only stdlib + ``yaml`` + ``traittutor.runtime.home`` are imported here (no
services.llm / config imports) to avoid import cycles — this module is imported
lazily from inside ``ModelCatalogService.load()``.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

from traittutor.runtime.home import PACKAGE_ROOT

__all__ = ["local_models_path", "load_local_llm"]


_ENV_PATTERN = re.compile(r"^env\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)$")


def local_models_path() -> Path:
    """Repo-root ``config/models.local.yaml`` (gitignored; holds real keys)."""
    return PACKAGE_ROOT / "config" / "models.local.yaml"


def _resolve_secret(value: Any) -> str:
    """Resolve ``env(VAR)`` -> ``os.environ[VAR]``; literals pass through."""
    if value is None:
        return ""
    text = str(value).strip()
    match = _ENV_PATTERN.match(text)
    if match:
        return os.getenv(match.group(1), "")
    return text


def _entry_to_profile(entry: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(entry, dict):
        return None
    entry_id = str(entry.get("id") or "").strip()
    model_value = str(entry.get("model") or "").strip()
    if not entry_id or not model_value:
        return None
    name = str(entry.get("name") or entry_id).strip() or entry_id
    model: dict[str, Any] = {
        "id": entry_id,
        "name": str(entry.get("name") or model_value).strip() or model_value,
        "model": model_value,
    }
    context_window = entry.get("context_window")
    if isinstance(context_window, int) and context_window > 0:
        model["context_window"] = context_window
    return {
        "id": entry_id,
        "name": name,
        "binding": str(entry.get("binding") or "custom").strip() or "custom",
        "base_url": str(entry.get("base_url") or "").strip(),
        "api_key": _resolve_secret(entry.get("api_key")),
        "api_version": str(entry.get("api_version") or "").strip(),
        "extra_headers": dict(entry.get("extra_headers") or {}),
        "models": [model],
    }


def load_local_llm() -> dict[str, Any] | None:
    """Load code-defined llm models, or ``None`` if absent/empty/invalid.

    Returns the ``services.llm`` sub-shape:
    ``{"active_profile_id", "active_model_id", "profiles": [...]}``.
    """
    path = local_models_path()
    try:
        if not path.exists():
            return None
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(raw, dict):
        return None

    entries = raw.get("models")
    if not isinstance(entries, list):
        return None

    profiles: list[dict[str, Any]] = []
    for entry in entries:
        profile = _entry_to_profile(entry)
        if profile is not None:
            profiles.append(profile)
    if not profiles:
        return None

    active = str(raw.get("active") or "").strip()
    if not active or not any(p["id"] == active for p in profiles):
        active = profiles[0]["id"]
    return {
        "active_profile_id": active,
        "active_model_id": active,
        "profiles": profiles,
    }
