"""API-level invariants for the invite-only registration + bootstrap flow.

The identity layer (``save_user`` never promotes, ``save_initial_admin`` is the
only admin path) is pinned in ``test_registration_invite_only.py``. These tests
lock the *router* half of the same security contract so the thin wrapper cannot
silently reintroduce a public-admin race or leak deployment credentials:

* ``POST /register`` is gated by ``allow_public_registration`` (off by default)
  and never mints an admin.
* ``POST /bootstrap`` is the sole admin-creation path, authorized only by the
  deployment secret, and refuses once any user exists.
* ``GET /bootstrap`` reports setup state without echoing the secret.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def auth_app(monkeypatch):
    """Mount the auth router on a minimal app with auth force-enabled.

    Every module-level constant the endpoints read is patched on the router
    module so the tests never depend on the host's real ``data/`` state.
    """
    from traittutor.api.routers import auth as auth_router

    monkeypatch.setattr(auth_router, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth_router, "POCKETBASE_ENABLED", False)
    app = FastAPI()
    app.include_router(auth_router.router, prefix="/api/v1/auth")
    return app, auth_router


# ---------------------------------------------------------------------------
# POST /register — invite-only gating
# ---------------------------------------------------------------------------


def test_register_refused_when_public_registration_disabled(auth_app, monkeypatch) -> None:
    app, auth_router = auth_app
    monkeypatch.setattr(auth_router, "load_auth_settings", lambda: {"allow_public_registration": False})

    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/auth/register",
            json={"username": "alice", "password": "password1234"},
        )

    assert resp.status_code == 403


def test_register_refused_when_auth_disabled(auth_app, monkeypatch) -> None:
    """With auth off, registration is unavailable (400), not a public opening."""
    app, auth_router = auth_app
    monkeypatch.setattr(auth_router, "AUTH_ENABLED", False)

    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/auth/register",
            json={"username": "alice", "password": "password1234"},
        )

    assert resp.status_code == 400


def test_register_creates_user_role_never_admin(
    auth_app, monkeypatch, mu_isolated_root
) -> None:
    pytest.importorskip("bcrypt")  # add_user → hash_password dep
    app, auth_router = auth_app
    monkeypatch.setattr(auth_router, "load_auth_settings", lambda: {"allow_public_registration": True})

    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/auth/register",
            json={"username": "alice", "password": "password1234"},
        )

    assert resp.status_code == 201
    from traittutor.multi_user.identity import list_user_info

    users = {u["username"]: u for u in list_user_info()}
    assert users["alice"]["role"] == "user"  # never auto-promoted to admin


def test_register_rejects_duplicate_username(
    auth_app, monkeypatch, mu_isolated_root, seed_user
) -> None:
    pytest.importorskip("bcrypt")
    app, auth_router = auth_app
    monkeypatch.setattr(auth_router, "load_auth_settings", lambda: {"allow_public_registration": True})
    seed_user("alice", role="user")

    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/auth/register",
            json={"username": "alice", "password": "password1234"},
        )

    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# POST /bootstrap — sole admin-creation path
# ---------------------------------------------------------------------------


def test_bootstrap_creates_first_admin_with_credentials(
    auth_app, monkeypatch, mu_isolated_root
) -> None:
    pytest.importorskip("bcrypt")
    app, auth_router = auth_app
    monkeypatch.setattr(auth_router, "INITIAL_ADMIN_USERNAME", "root")
    monkeypatch.setattr(auth_router, "INITIAL_ADMIN_PASSWORD", "deploy-secret")

    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/auth/bootstrap",
            json={"username": "root", "password": "deploy-secret"},
        )

    assert resp.status_code == 201
    from traittutor.multi_user.identity import list_user_info

    users = {u["username"]: u for u in list_user_info()}
    assert users["root"]["role"] == "admin"


def test_bootstrap_creates_first_admin_with_token(
    auth_app, monkeypatch, mu_isolated_root
) -> None:
    """The bootstrap token authorizes admin creation without a fixed username."""
    pytest.importorskip("bcrypt")
    app, auth_router = auth_app
    monkeypatch.setattr(auth_router, "INITIAL_ADMIN_USERNAME", "")
    monkeypatch.setattr(auth_router, "INITIAL_ADMIN_PASSWORD", "")
    monkeypatch.setattr(auth_router, "INITIAL_ADMIN_BOOTSTRAP_TOKEN", "boot-token-abc")

    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/auth/bootstrap",
            json={"username": "root", "password": "deploy-secret", "token": "boot-token-abc"},
        )

    assert resp.status_code == 201
    from traittutor.multi_user.identity import list_user_info

    users = {u["username"]: u for u in list_user_info()}
    assert users["root"]["role"] == "admin"


def test_bootstrap_refuses_wrong_credentials(auth_app, monkeypatch, mu_isolated_root) -> None:
    app, auth_router = auth_app
    monkeypatch.setattr(auth_router, "INITIAL_ADMIN_USERNAME", "root")
    monkeypatch.setattr(auth_router, "INITIAL_ADMIN_PASSWORD", "deploy-secret")

    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/auth/bootstrap",
            json={"username": "root", "password": "wrong-secret"},
        )

    assert resp.status_code == 403


def test_bootstrap_refused_when_auth_disabled(auth_app, monkeypatch, mu_isolated_root) -> None:
    app, auth_router = auth_app
    monkeypatch.setattr(auth_router, "AUTH_ENABLED", False)
    monkeypatch.setattr(auth_router, "INITIAL_ADMIN_USERNAME", "root")
    monkeypatch.setattr(auth_router, "INITIAL_ADMIN_PASSWORD", "deploy-secret")

    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/auth/bootstrap",
            json={"username": "root", "password": "deploy-secret"},
        )

    assert resp.status_code == 400


def test_bootstrap_refuses_once_store_is_nonempty(
    auth_app, monkeypatch, mu_isolated_root, seed_user
) -> None:
    """Once any user exists, bootstrap may not mint a second administrator."""
    pytest.importorskip("bcrypt")
    app, auth_router = auth_app
    monkeypatch.setattr(auth_router, "INITIAL_ADMIN_USERNAME", "root")
    monkeypatch.setattr(auth_router, "INITIAL_ADMIN_PASSWORD", "deploy-secret")
    seed_user("someone", role="user")

    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/auth/bootstrap",
            json={"username": "root", "password": "deploy-secret"},
        )

    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# GET /bootstrap — setup state must not leak the deployment secret
# ---------------------------------------------------------------------------


def test_bootstrap_status_reports_state_without_leaking_secret(
    auth_app, monkeypatch, mu_isolated_root
) -> None:
    app, auth_router = auth_app
    monkeypatch.setattr(auth_router, "INITIAL_ADMIN_USERNAME", "root")
    monkeypatch.setattr(auth_router, "INITIAL_ADMIN_PASSWORD", "deploy-secret")

    with TestClient(app) as client:
        resp = client.get("/api/v1/auth/bootstrap")

    assert resp.status_code == 200
    body = resp.json()
    assert body["initialized"] is False
    assert body["bootstrap_configured"] is True
    assert "deploy-secret" not in resp.text
    assert "root" not in resp.text  # username is also a credential here
