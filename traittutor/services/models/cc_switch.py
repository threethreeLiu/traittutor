"""Pure mapper from CC Switch provider rows to model records.

CC Switch stores providers in SQLite (``~/.cc-switch/cc-switch.db``). This
module reads the ``providers`` table and maps each row to a :class:`ModelRecord`
suitable for TraitTutor's ``custom_anthropic`` (claude app_type) and ``custom``
(codex app_type) bindings. OAuth-only providers (e.g. gemini) map to ``None``.

These functions are pure: no logging, no writes beyond the stdlib DB read.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import json
from pathlib import Path
import re
import sqlite3
import tomllib
from typing import Any

__all__ = [
    "ModelRecord",
    "map_provider",
    "read_providers",
    "iter_model_records",
]


@dataclass(frozen=True)
class ModelRecord:
    """One CC Switch provider mapped onto a TraitTutor model route."""

    id: str
    name: str
    binding: str  # "custom_anthropic" (claude) or "custom" (codex)
    base_url: str
    api_key: str
    model: str  # clean model id, no ``[..]`` context-window suffix
    extra_headers: dict[str, str] = field(default_factory=dict)
    api_version: str = ""


# --- small text helpers -----------------------------------------------------

_BRACKET_SUFFIX = re.compile(r"\s*\[[^\]]*\]\s*$")
_CLEAN_SLUG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)


def _strip_model_suffix(model: Any) -> str:
    """Strip trailing ``[..]`` context-window markers (e.g. ``x[1M]`` -> ``x``).

    Non-string values (e.g. a TOML ``[model]`` table parsed as a dict) coerce to
    ``""`` so one odd provider config never crashes the whole import.
    """
    if not isinstance(model, str) or not model:
        return ""
    prev: str | None = None
    cur = model.strip()
    while cur and cur != prev:
        prev = cur
        cur = _BRACKET_SUFFIX.sub("", cur).strip()
    return cur


def _slugify(name: str | None) -> str:
    """Lowercase, non-alnum runs -> single ``-``, trimmed."""
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower())
    s = re.sub(r"-+", "-", s).strip("-")
    return s or "provider"


def _looks_like_uuid(value: str | None) -> bool:
    return bool(_UUID.match(value or ""))


def _is_clean_slug(value: str | None) -> bool:
    return bool(_CLEAN_SLUG.match(value or ""))


def _resolve_record_id(provider_id: str | None, name: str | None) -> str:
    """Prefer the DB id when it is a clean slug; UUIDs fall back to slugified name."""
    pid = (provider_id or "").strip()
    if pid and not _looks_like_uuid(pid) and _is_clean_slug(pid):
        return pid
    return _slugify(name)


# --- per-app_type mappers ---------------------------------------------------


def _resolve_claude_model(settings_config: dict[str, Any], env: dict[str, Any]) -> str:
    """First non-empty candidate; ``[..]`` always stripped.

    Priority: top-level ``model`` -> ``env.ANTHROPIC_MODEL`` -> any
    ``env.ANTHROPIC_DEFAULT_*_MODEL_NAME`` (already clean) -> any
    ``env.ANTHROPIC_DEFAULT_*_MODEL`` (suffix stripped).
    """
    model = _strip_model_suffix(settings_config.get("model"))
    if model:
        return model

    model = _strip_model_suffix(env.get("ANTHROPIC_MODEL"))
    if model:
        return model

    for key in sorted(env):
        if key.startswith("ANTHROPIC_DEFAULT_") and key.endswith("_MODEL_NAME"):
            val = _strip_model_suffix(env.get(key))
            if val:
                return val

    for key in sorted(env):
        if key.startswith("ANTHROPIC_DEFAULT_") and key.endswith("_MODEL"):
            val = _strip_model_suffix(env.get(key))
            if val:
                return val

    return ""


def _map_claude(name: str, settings_config: dict[str, Any]) -> ModelRecord | None:
    env = settings_config.get("env")
    if not isinstance(env, dict):
        env = {}
    base_url = str(env.get("ANTHROPIC_BASE_URL") or "").strip()
    api_key = str(env.get("ANTHROPIC_AUTH_TOKEN") or env.get("ANTHROPIC_API_KEY") or "").strip()
    if not base_url or not api_key:
        return None
    return ModelRecord(
        id=_slugify(name),
        name=name or "",
        binding="custom_anthropic",
        base_url=base_url,
        api_key=api_key,
        model=_resolve_claude_model(settings_config, env),
    )


def _map_codex(name: str, settings_config: dict[str, Any]) -> ModelRecord | None:
    auth = settings_config.get("auth")
    if not isinstance(auth, dict):
        auth = {}
    api_key = str(auth.get("OPENAI_API_KEY") or "").strip()
    if not api_key:
        return None

    base_url = ""
    model = ""
    config_str = settings_config.get("config")
    if config_str:
        try:
            parsed = tomllib.loads(config_str if isinstance(config_str, str) else str(config_str))
        except Exception:
            parsed = {}
        model = _strip_model_suffix(parsed.get("model"))
        providers = parsed.get("model_providers")
        if isinstance(providers, dict):
            for prov in providers.values():
                if isinstance(prov, dict) and prov.get("base_url"):
                    base_url = str(prov["base_url"]).strip()
                    break

    if not base_url:
        return None
    return ModelRecord(
        id=_slugify(name),
        name=name or "",
        binding="custom",
        base_url=base_url,
        api_key=api_key,
        model=model,
    )


def map_provider(
    app_type: str,
    name: str,
    settings_config: dict[str, Any],
) -> ModelRecord | None:
    """Map one CC Switch provider to a ModelRecord, or ``None`` if it has no
    usable static key/endpoint (e.g. OAuth-only gemini, or missing fields)."""
    if not isinstance(settings_config, dict):
        settings_config = {}
    app = (app_type or "").strip().lower()
    if app == "claude":
        return _map_claude(name, settings_config)
    if app == "codex":
        return _map_codex(name, settings_config)
    return None


# --- sqlite reader ----------------------------------------------------------

_SELECT = "SELECT id, app_type, name, settings_config, is_current FROM providers"


def read_providers(db_path: str | Path) -> list[dict[str, Any]]:
    """Read the ``providers`` table via stdlib sqlite3.

    Returns rows as dicts with keys: ``id``, ``app_type``, ``name``,
    ``settings_config`` (parsed dict; ``{}`` on parse failure), ``is_current`` (bool).
    Returns an empty list if the DB or table is absent.
    """
    rows: list[dict[str, Any]] = []
    conn = sqlite3.connect(str(db_path))
    try:
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.execute(_SELECT)
        except sqlite3.OperationalError:
            # DB or table missing — nothing to map.
            return rows
        for row in cursor.fetchall():
            raw = row["settings_config"]
            try:
                parsed = json.loads(raw) if raw else {}
            except Exception:
                parsed = {}
            if not isinstance(parsed, dict):
                parsed = {}
            rows.append(
                {
                    "id": row["id"],
                    "app_type": row["app_type"],
                    "name": row["name"],
                    "settings_config": parsed,
                    "is_current": bool(row["is_current"]),
                }
            )
    finally:
        conn.close()
    return rows


def iter_model_records(db_path: str | Path) -> list[tuple[ModelRecord, bool]]:
    """Read providers and map each; skip unmappable rows.

    Returns ``(record, is_current)`` pairs where ``is_current`` reflects the DB
    row and the record id prefers the DB id when it is a clean (non-UUID) slug.
    """
    out: list[tuple[ModelRecord, bool]] = []
    for row in read_providers(db_path):
        record = map_provider(row["app_type"], row["name"], row["settings_config"])
        if record is None:
            continue
        final_id = _resolve_record_id(row["id"], row["name"])
        if final_id != record.id:
            record = replace(record, id=final_id)
        out.append((record, bool(row["is_current"])))
    return out
