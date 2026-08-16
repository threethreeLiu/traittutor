"""
SQLite store for skill-hub publish credentials
===============================================

Per-hub bearer tokens minted by ``skill login`` (browser OAuth) and consumed by
``skill publish`` / ``skill update``. Kept in the settings dir as
the canonical owner-scoped database. Tokens remain separate from public hub
endpoint metadata because they are secrets.

Resolution order at publish time stays: explicit ``--token`` → env
(``TRAITTUTOR_HUB_TOKEN`` / ``EDUHUB_TOKEN``) → this store.
"""

from __future__ import annotations

from typing import Any

from traittutor.multi_user.models import LOCAL_ADMIN_ID
from traittutor.services.path_service import get_path_service
from traittutor.unified_storage import SQLiteDocumentStore


def _store() -> SQLiteDocumentStore:
    return SQLiteDocumentStore(
        LOCAL_ADMIN_ID,
        namespace="skill-hub-auth",
        path_service=get_path_service(),
    )


def _load() -> dict[str, Any]:
    data = _store().load("tokens", {})
    return data if isinstance(data, dict) else {}


def get_stored_token(hub: str) -> str | None:
    """The saved bearer token for ``hub``, or None."""
    entry = (_load().get("tokens") or {}).get(hub)
    if isinstance(entry, dict):
        token = str(entry.get("token") or "").strip()
        return token or None
    return None


def get_stored_identity(hub: str) -> dict[str, Any] | None:
    """The saved login/name snapshot for ``hub`` (for ``whoami``-style display)."""
    entry = (_load().get("tokens") or {}).get(hub)
    return entry if isinstance(entry, dict) else None


def store_token(
    hub: str,
    token: str,
    *,
    login: str | None = None,
    name: str | None = None,
) -> None:
    """Persist (and overwrite) the token for ``hub``."""
    data = _load()
    tokens = data.get("tokens")
    if not isinstance(tokens, dict):
        tokens = {}
        data["tokens"] = tokens
    tokens[hub] = {"token": token, "login": login, "name": name}
    _store().save("tokens", data)


def clear_token(hub: str) -> bool:
    """Remove the saved token for ``hub``; returns whether one was present."""
    data = _load()
    tokens = data.get("tokens")
    if not isinstance(tokens, dict) or hub not in tokens:
        return False
    del tokens[hub]
    _store().save("tokens", data)
    return True


__all__ = ["clear_token", "get_stored_identity", "get_stored_token", "store_token"]
