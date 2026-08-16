"""
Interface (UI) settings reader.

This is the canonical backend source for user-selected UI language/theme.
"""

from __future__ import annotations

from typing import Any

from traittutor.multi_user.context import get_current_user
from traittutor.services.path_service import get_path_service
from traittutor.unified_storage import SectionedRecordStore

DEFAULT_UI_SETTINGS: dict[str, Any] = {
    # Snow is the product default; users can still choose any supported theme.
    "theme": "snow",
    "language": "zh",
}


def _interface_settings_file():
    # Resolved on every call so a per-user PathService (set after auth)
    # routes reads to the caller's own ``settings/interface.json`` instead
    # of the admin scope frozen at import time.
    return get_path_service().get_settings_file("interface")


def _saved_settings() -> dict[str, Any] | None:
    record = next(
        iter(
            SectionedRecordStore(
                "interface_settings",
                get_current_user().id,
                schema_version=1,
                path_service=get_path_service(),
            ).snapshot()["settings"]
        ),
        None,
    )
    return dict(record.get("value") or {}) if record else None


def _normalize_language(language: Any, default: str = "en") -> str:
    """
    Normalize language codes:
    - en/english -> en
    - zh/chinese/cn -> zh
    """
    if language is None or language == "":
        language = default

    if isinstance(language, str):
        s = language.lower().strip()
        if s in {"en", "english"}:
            return "en"
        if s in {"zh", "chinese", "cn"}:
            return "zh"

    # Fall back to default
    if isinstance(default, str):
        return _normalize_language(default, "en")
    return "en"


def get_ui_settings() -> dict[str, Any]:
    """
    Read UI settings from interface.json with defaults.

    Returns:
        dict containing at least: {"theme": "...", "language": "..."}
    """
    saved = _saved_settings()
    if saved is None:
        return DEFAULT_UI_SETTINGS.copy()
    merged = {**DEFAULT_UI_SETTINGS, **saved}
    if merged.get("theme") == "glass":
        merged["theme"] = "snow"
    merged["language"] = _normalize_language(
        merged.get("language"), DEFAULT_UI_SETTINGS["language"]
    )
    return merged


def get_ui_language(default: str = "en") -> str:
    """
    Get current UI language.

    Priority:
    1) interface.json
    2) provided default
    3) 'en'
    """
    # ``get_ui_settings`` intentionally fills its UI-facing Chinese default.
    # Generation callers, however, pass their own fallback and must receive it
    # until this user has actually stored an interface preference.
    if _saved_settings() is None:
        return _normalize_language(default, "en")

    settings = get_ui_settings()
    return _normalize_language(settings.get("language"), default)
