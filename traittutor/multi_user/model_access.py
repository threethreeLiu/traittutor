"""Server-side model grant resolution and redacted model views.

Grants carry LLM assignments only (grant v2): embedding and search always
resolve from the deployment's active profiles, so per-user grants for them
were never enforced and are not stored.
"""

from __future__ import annotations

from typing import Any

from traittutor.services.config.model_catalog import ModelCatalogService
from traittutor.services.model_selection import list_llm_options

from .context import get_current_user
from .paths import get_admin_path_service


def admin_catalog_service() -> ModelCatalogService:
    return ModelCatalogService(path=get_admin_path_service().get_settings_file("model_catalog"))


def admin_catalog() -> dict[str, Any]:
    return admin_catalog_service().load()


def _profile_by_id(catalog: dict[str, Any], service: str, profile_id: str) -> dict[str, Any] | None:
    for profile in catalog.get("services", {}).get(service, {}).get("profiles", []) or []:
        if str(profile.get("id") or "") == profile_id:
            return profile
    return None


def _model_by_id(profile: dict[str, Any], model_id: str) -> dict[str, Any] | None:
    for model in profile.get("models", []) or []:
        if str(model.get("id") or "") == model_id:
            return model
    return None


def redacted_model_access(user_id: str | None = None) -> dict[str, list[dict[str, Any]]]:
    """All code-defined LLM models are visible to every user (no per-user grants)."""
    options = list_llm_options(admin_catalog()).get("options", [])
    items = [
        {
            "profile_id": opt.get("profile_id"),
            "model_id": opt.get("model_id"),
            "name": opt.get("model_name") or opt.get("model") or opt.get("model_id"),
            "model": opt.get("model") or "",
            "source": "code",
            "available": True,
        }
        for opt in options
    ]
    return {"llm": items}


def allowed_llm_options() -> dict[str, Any]:
    """Every user selects from the full code-defined model list."""
    return list_llm_options(admin_catalog())


def has_capability_access(capability: str, user_id: str | None = None) -> bool:
    """Whether the user has at least one usable model for ``capability``.

    Mirrors :func:`redacted_model_access` so the server-side gate and the
    frontend lock always agree. LLM models are code-defined and visible to every
    user, so this is ``True`` for any non-admin as long as at least one code
    model exists. Admins are never gated.
    """
    user = get_current_user()
    if user.is_admin:
        return True
    if user_id is None:
        user_id = user.id
    items = redacted_model_access(user_id).get(capability, []) or []
    return any(item.get("available") for item in items)


def apply_allowed_llm_selection(selection: dict[str, Any] | None) -> dict[str, Any] | None:
    """Accept any selection that names a real code-defined model."""
    if not selection:
        return selection
    profile_id = str(selection.get("profile_id") or "")
    model_id = str(selection.get("model_id") or "")
    for opt in list_llm_options(admin_catalog()).get("options", []):
        if (
            str(opt.get("profile_id") or "") == profile_id
            and str(opt.get("model_id") or "") == model_id
        ):
            return selection
    raise PermissionError("This model is not configured.")
