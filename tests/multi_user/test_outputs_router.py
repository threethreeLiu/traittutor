"""Router-level invariants for the authenticated outputs download endpoint.

``api/routers/outputs.py`` replaced the unauthenticated ``/api/outputs``
static mount. The path whitelist itself is unit-tested in
``tests/multi_user/test_path_service.py``; these tests pin the HTTP boundary:

* unauthenticated callers get 401 (the old mount served files anonymously),
* whitelisted artifacts are served with a ``nosniff`` header,
* existing but non-whitelisted files and private suffixes stay 404,
* one user's artifacts are not reachable through another user's session.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def outputs_env(monkeypatch, mu_isolated_root):
    """Mount the outputs router behind require_auth, over an isolated store."""
    from traittutor.api.routers import auth as auth_router
    from traittutor.api.routers import outputs as outputs_module
    from traittutor.services import auth as auth_service

    monkeypatch.setattr(auth_router, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth_router, "POCKETBASE_ENABLED", False)
    monkeypatch.setattr(auth_service, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth_service, "AUTH_SECRET", "test-secret")
    monkeypatch.setattr(auth_service, "POCKETBASE_ENABLED", False)

    app = FastAPI()
    app.include_router(
        outputs_module.router,
        prefix="/api/outputs",
        dependencies=[Depends(auth_router.require_auth)],
    )
    return app, auth_service


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _user_data_dir(mu_isolated_root: Path, user_id: str) -> Path:
    """Mirror of PathService(workspace_root=USERS_ROOT/uid)._user_data_dir."""
    return mu_isolated_root / "data" / "users" / user_id / "user"


def _write_artifact(root: Path, relative: str, content: bytes = b"data") -> None:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)


def test_unauthenticated_download_is_rejected(outputs_env) -> None:
    app, _ = outputs_env

    with TestClient(app) as client:
        resp = client.get("/api/outputs/workspace/co-writer/audio/clip.mp3")

    assert resp.status_code == 401


def test_whitelisted_artifact_is_served_with_nosniff(
    outputs_env, seed_user, mu_isolated_root
) -> None:
    pytest.importorskip("bcrypt")
    app, auth_service = outputs_env
    alice = seed_user("alice", role="user")
    _write_artifact(
        _user_data_dir(mu_isolated_root, alice["id"]),
        "workspace/co-writer/audio/clip.mp3",
        b"mp3-bytes",
    )
    token = auth_service.create_token("alice")

    with TestClient(app) as client:
        resp = client.get(
            "/api/outputs/workspace/co-writer/audio/clip.mp3",
            headers=_bearer(token),
        )

    assert resp.status_code == 200
    assert resp.content == b"mp3-bytes"
    assert resp.headers["X-Content-Type-Options"] == "nosniff"


def test_existing_but_non_whitelisted_file_is_404(
    outputs_env, seed_user, mu_isolated_root
) -> None:
    pytest.importorskip("bcrypt")
    app, auth_service = outputs_env
    alice = seed_user("alice", role="user")
    _write_artifact(
        _user_data_dir(mu_isolated_root, alice["id"]),
        "workspace/private/notes.txt",
    )
    token = auth_service.create_token("alice")

    with TestClient(app) as client:
        resp = client.get(
            "/api/outputs/workspace/private/notes.txt",
            headers=_bearer(token),
        )

    assert resp.status_code == 404


def test_private_suffix_under_whitelisted_prefix_is_404(
    outputs_env, seed_user, mu_isolated_root
) -> None:
    """A whitelisted directory must not leak private file types (.json etc.)."""
    pytest.importorskip("bcrypt")
    app, auth_service = outputs_env
    alice = seed_user("alice", role="user")
    _write_artifact(
        _user_data_dir(mu_isolated_root, alice["id"]),
        "workspace/co-writer/audio/session.json",
    )
    token = auth_service.create_token("alice")

    with TestClient(app) as client:
        resp = client.get(
            "/api/outputs/workspace/co-writer/audio/session.json",
            headers=_bearer(token),
        )

    assert resp.status_code == 404


def test_artifacts_are_isolated_per_user(
    outputs_env, seed_user, mu_isolated_root
) -> None:
    """Bob's artifact must not be reachable through Alice's session."""
    pytest.importorskip("bcrypt")
    app, auth_service = outputs_env
    alice = seed_user("alice", role="user")
    bob = seed_user("bob", role="user")
    _write_artifact(
        _user_data_dir(mu_isolated_root, bob["id"]),
        "workspace/co-writer/audio/clip.mp3",
    )
    token = auth_service.create_token("alice")

    with TestClient(app) as client:
        resp = client.get(
            "/api/outputs/workspace/co-writer/audio/clip.mp3",
            headers=_bearer(token),
        )

    assert resp.status_code == 404
