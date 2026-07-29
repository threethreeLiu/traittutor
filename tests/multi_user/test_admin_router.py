"""Router-level invariants for the admin control plane (api/routers/admin.py).

These endpoints sit behind ``Depends(require_admin)`` and are the most
privileged surface in the product. The identity-layer behavior is pinned in
``test_registration_invite_only.py`` / ``test_session_invalidation.py``; these
tests lock the HTTP boundary so a regression cannot silently reopen it:

* unauthenticated and non-admin callers are rejected (401 / 403),
* an admin can list users and change *other* accounts,
* an admin cannot change their own access through this surface,
* the model catalog is redacted before it leaves the server.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def admin_env(monkeypatch, mu_isolated_root):
    """Mount the admin router with auth force-enabled over an isolated store.

    ``admin.py`` and ``audit.py`` bind the path constants at import time, so
    they are re-pointed at the isolated tree explicitly — nothing may touch
    the developer's real ``data/`` directory.
    """
    from traittutor.api.routers import admin as admin_module
    from traittutor.api.routers import auth as auth_router
    from traittutor.multi_user import audit, paths
    from traittutor.services import auth as auth_service

    monkeypatch.setattr(auth_router, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth_router, "POCKETBASE_ENABLED", False)
    monkeypatch.setattr(auth_service, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth_service, "AUTH_SECRET", "test-secret")
    monkeypatch.setattr(auth_service, "POCKETBASE_ENABLED", False)
    monkeypatch.setattr(admin_module, "SYSTEM_ROOT", paths.SYSTEM_ROOT)
    monkeypatch.setattr(admin_module, "USERS_ROOT", paths.USERS_ROOT)
    monkeypatch.setattr(audit, "SYSTEM_ROOT", paths.SYSTEM_ROOT)

    app = FastAPI()
    app.include_router(admin_module.router, prefix="/api/v1/admin")
    return app, admin_module, auth_service


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_unauthenticated_requests_are_rejected(admin_env) -> None:
    app, _, _ = admin_env

    with TestClient(app) as client:
        assert client.get("/api/v1/admin/users").status_code == 401
        assert client.get("/api/v1/admin/overview").status_code == 401
        assert client.get("/api/v1/admin/models").status_code == 401


def test_non_admin_user_is_forbidden(admin_env, seed_user) -> None:
    pytest.importorskip("bcrypt")
    app, _, auth_service = admin_env
    seed_user("alice", role="user")
    token = auth_service.create_token("alice")

    with TestClient(app) as client:
        resp = client.get("/api/v1/admin/users", headers=_bearer(token))

    assert resp.status_code == 403


def test_admin_can_list_users(admin_env, seed_user) -> None:
    pytest.importorskip("bcrypt")
    app, _, auth_service = admin_env
    root = seed_user("root", role="admin")
    seed_user("alice", role="user")
    token = auth_service.create_token("root")

    with TestClient(app) as client:
        resp = client.get("/api/v1/admin/users", headers=_bearer(token))

    assert resp.status_code == 200
    usernames = {u["username"] for u in resp.json()["users"]}
    assert {"root", "alice"} <= usernames
    assert root["role"] == "admin"


def test_admin_can_set_role_on_other_user(admin_env, seed_user) -> None:
    pytest.importorskip("bcrypt")
    app, _, auth_service = admin_env
    seed_user("root", role="admin")
    alice = seed_user("alice", role="user")
    token = auth_service.create_token("root")

    with TestClient(app) as client:
        resp = client.post(
            f"/api/v1/admin/users/{alice['id']}/action",
            json={"action": "set_role", "role": "admin"},
            headers=_bearer(token),
        )

    assert resp.status_code == 200
    from traittutor.multi_user.identity import get_user_by_id

    found = get_user_by_id(alice["id"])
    assert found is not None and found[1]["role"] == "admin"


def test_admin_cannot_change_own_access(admin_env, seed_user) -> None:
    pytest.importorskip("bcrypt")
    app, _, auth_service = admin_env
    root = seed_user("root", role="admin")
    token = auth_service.create_token("root")

    with TestClient(app) as client:
        for payload in (
            {"action": "set_role", "role": "user"},
            {"action": "set_disabled", "disabled": True},
        ):
            resp = client.post(
                f"/api/v1/admin/users/{root['id']}/action",
                json=payload,
                headers=_bearer(token),
            )
            assert resp.status_code == 400, payload

    # Self-protection must not be a silent no-op either: access is intact.
    from traittutor.multi_user.identity import get_user_by_id

    found = get_user_by_id(root["id"])
    assert found is not None
    assert found[1]["role"] == "admin"
    assert found[1]["disabled"] is False


def test_action_on_unknown_user_is_404(admin_env, seed_user) -> None:
    pytest.importorskip("bcrypt")
    app, _, auth_service = admin_env
    seed_user("root", role="admin")
    token = auth_service.create_token("root")

    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/admin/users/u_missing/action",
            json={"action": "set_role", "role": "admin"},
            headers=_bearer(token),
        )

    assert resp.status_code == 404


def test_models_catalog_secrets_are_redacted(admin_env, seed_user, monkeypatch) -> None:
    pytest.importorskip("bcrypt")
    app, admin_module, auth_service = admin_env
    seed_user("root", role="admin")
    token = auth_service.create_token("root")

    class _StubCatalog:
        def load(self) -> dict:
            return {
                "services": {
                    "llm": {
                        "profiles": [
                            {
                                "name": "primary",
                                "api_key": "sk-1234567890abcdef",
                                "access_token": "tok_secretvalue",
                            }
                        ]
                    }
                }
            }

    monkeypatch.setattr(admin_module, "get_model_catalog_service", lambda: _StubCatalog())

    with TestClient(app) as client:
        resp = client.get("/api/v1/admin/models", headers=_bearer(token))

    assert resp.status_code == 200
    profile = resp.json()["catalog"]["services"]["llm"]["profiles"][0]
    assert profile["name"] == "primary"  # non-sensitive fields pass through
    assert profile["api_key"] == "sk-1...cdef"
    assert profile["access_token"] == "tok_...alue"
    assert "sk-1234567890abcdef" not in resp.text
    assert "tok_secretvalue" not in resp.text
