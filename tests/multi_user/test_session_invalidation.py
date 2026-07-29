"""Session-revocation invariants for the token-version security model.

Account state changes that must invalidate outstanding sessions:
  * disabling an account,
  * an explicit session-invalidation call,
  * a password change.

Each bumps the account ``token_version``; ``decode_token`` rejects any token
whose embedded version is stale, and ``authenticate`` rejects disabled accounts.
These tests pin that contract so a regression cannot leave revoked sessions live.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def auth_mod(monkeypatch, mu_isolated_root):
    """Force the standard JWT path on, over an isolated user store."""
    from traittutor.services import auth as auth_service

    monkeypatch.setattr(auth_service, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth_service, "AUTH_SECRET", "test-secret")
    monkeypatch.setattr(auth_service, "POCKETBASE_ENABLED", False)
    return auth_service


def _seed_and_token(auth_mod, seed_user, username="alice", password="password1234") -> str:
    seed_user(username, password=password, role="user")
    token = auth_mod.create_token(username, "user")
    assert auth_mod.decode_token(token) is not None  # baseline: token is valid
    return token


def test_disabled_account_token_is_rejected(auth_mod, seed_user) -> None:
    pytest.importorskip("bcrypt")  # seed_user → hash_password dep
    from traittutor.multi_user.identity import set_disabled

    token = _seed_and_token(auth_mod, seed_user)

    assert set_disabled("alice", True) is True

    assert auth_mod.decode_token(token) is None
    assert auth_mod.authenticate("alice", "password1234") is None


def test_invalidate_sessions_rejects_outstanding_tokens(auth_mod, seed_user) -> None:
    pytest.importorskip("bcrypt")
    from traittutor.multi_user.identity import invalidate_sessions

    token = _seed_and_token(auth_mod, seed_user)

    assert invalidate_sessions("alice") is True

    assert auth_mod.decode_token(token) is None
    # A freshly issued token is accepted again.
    fresh = auth_mod.create_token("alice", "user")
    assert auth_mod.decode_token(fresh) is not None


def test_password_change_rejects_outstanding_tokens(auth_mod, seed_user) -> None:
    pytest.importorskip("bcrypt")
    from traittutor.multi_user.identity import update_password_hash
    from traittutor.services.auth import hash_password

    token = _seed_and_token(auth_mod, seed_user)

    assert update_password_hash("alice", hash_password("new-password-1234")) is True

    assert auth_mod.decode_token(token) is None
    # The new password authenticates; the old one no longer does.
    assert auth_mod.authenticate("alice", "new-password-1234") is not None
    assert auth_mod.authenticate("alice", "password1234") is None


def test_re_enabling_account_requires_fresh_token(auth_mod, seed_user) -> None:
    """Re-enabling also bumps token_version, so old sessions stay revoked."""
    pytest.importorskip("bcrypt")
    from traittutor.multi_user.identity import set_disabled

    token = _seed_and_token(auth_mod, seed_user)
    set_disabled("alice", True)
    set_disabled("alice", False)

    # The pre-disable token is stale (version bumped twice).
    assert auth_mod.decode_token(token) is None
    fresh = auth_mod.create_token("alice", "user")
    assert auth_mod.decode_token(fresh) is not None
    assert auth_mod.authenticate("alice", "password1234") is not None
