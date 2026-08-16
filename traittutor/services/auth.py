"""
Authentication service for TraitTutor.

Enabled by default: every API route except the auth router requires a valid JWT
session. Visitors are redirected to /login.

Public self-service registration is disabled by default
(`allow_public_registration=false` in `data/user/settings/auth.json`). The first
administrator is created explicitly via the POST /api/v1/auth/bootstrap endpoint,
authorized by the INITIAL_ADMIN_USERNAME/INITIAL_ADMIN_PASSWORD or
INITIAL_ADMIN_BOOTSTRAP_TOKEN environment variables — no account is ever
auto-promoted to admin by registration order. Additional users are managed by
an existing administrator.

For local single-user development, set enabled=false in
`data/user/settings/auth.json` to bypass login.

Quick setup (single user via data/user/settings/auth.json):
    1. Set enabled=true (now the default).
    2. Set username=<your username>
    3. Generate a password hash:
           python -c "from traittutor.services.auth import hash_password; print(hash_password('yourpassword'))"
       Paste the output into password_hash=<hash>

Multi-user setup (recommended):
    Leave username/password_hash empty. Export INITIAL_ADMIN_USERNAME and
    INITIAL_ADMIN_PASSWORD (or INITIAL_ADMIN_BOOTSTRAP_TOKEN), start the server,
    and POST to /api/v1/auth/bootstrap to create the first administrator. That
    admin can then manage other users from /admin/users.

    Users are stored in data/user/auth_users.json:
        {
            "alice": {"hash": "$2b$12$...", "role": "admin", "created_at": "2026-..."},
            "bob":   {"hash": "$2b$12$...", "role": "user",  "created_at": "2026-..."}
        }
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
import os
from typing import Any

from traittutor.services.config import load_auth_settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration — read once at import time from runtime JSON settings
# ---------------------------------------------------------------------------

_AUTH_SETTINGS = load_auth_settings()
AUTH_ENABLED: bool = bool(_AUTH_SETTINGS["enabled"])
AUTH_USERNAME: str = str(_AUTH_SETTINGS["username"])
AUTH_PASSWORD_HASH: str = str(_AUTH_SETTINGS["password_hash"])
AUTH_SECRET: str = ""
TOKEN_EXPIRE_HOURS: int = int(_AUTH_SETTINGS["token_expire_hours"])
INITIAL_ADMIN_USERNAME: str = os.environ.get("INITIAL_ADMIN_USERNAME", "").strip()
INITIAL_ADMIN_PASSWORD: str = os.environ.get("INITIAL_ADMIN_PASSWORD", "").strip()
INITIAL_ADMIN_BOOTSTRAP_TOKEN: str = os.environ.get("INITIAL_ADMIN_BOOTSTRAP_TOKEN", "")

_ALGORITHM = "HS256"


if AUTH_ENABLED and not AUTH_SECRET:
    from traittutor.multi_user.identity import load_or_create_auth_secret

    AUTH_SECRET = load_or_create_auth_secret()


# ---------------------------------------------------------------------------
# Token payload
# ---------------------------------------------------------------------------


@dataclass
class TokenPayload:
    """Decoded JWT payload."""

    username: str
    role: str
    user_id: str = ""


# ---------------------------------------------------------------------------
# Password hashing — uses bcrypt directly (passlib is unmaintained for bcrypt 4+)
# ---------------------------------------------------------------------------


def hash_password(plain: str) -> str:
    """Hash a plaintext password. Use this to generate password hashes."""
    import bcrypt

    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plaintext password against a stored bcrypt hash."""
    import bcrypt

    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False


# ---------------------------------------------------------------------------
# User store — multi-user JSON store plus optional auth.json bootstrap user
# ---------------------------------------------------------------------------


def _make_user_record(hashed: str, role: str = "user", created_at: str = "") -> dict[str, Any]:
    """Build a canonical user record dict for legacy callers/tests."""
    from traittutor.multi_user.identity import new_user_id

    return {
        "id": new_user_id(),
        "hash": hashed,
        "role": role,
        "created_at": created_at or datetime.now(timezone.utc).isoformat(),
        "disabled": False,
        "avatar": "",
    }


def _load_users() -> dict[str, dict]:
    """
    Load the user store, migrating old flat format if needed.

    Priority:
      1. multi-user identity store
      2. auth.json username + password_hash — single-user bootstrap user

    Old format: {"alice": "$2b$12$..."}
    New format: {"alice": {"hash": "...", "role": "admin", "created_at": "..."}}
    """
    from traittutor.multi_user.identity import load_users

    return load_users(AUTH_USERNAME, AUTH_PASSWORD_HASH)


def is_first_user() -> bool:
    """Return True when no durable user exists yet."""
    return len(_load_users()) == 0


def add_user(username: str, plain_password: str, role: str = "user") -> None:
    """
    Add or update a user in data/user/auth_users.json.

    The role defaults to 'user'. Admin creation is reserved for explicit
    deployment bootstrap or an existing administrator.

    Creates the file (and parent directories) if they don't exist.
    """
    from traittutor.multi_user.identity import save_user

    record = save_user(username, hash_password(plain_password), role=role)  # type: ignore[arg-type]
    logger.info("User '%s' saved with role=%r", username, record.get("role", "user"))


def create_initial_admin(username: str, plain_password: str) -> dict | None:
    """Atomically create the first administrator for the bootstrap endpoint.

    Returns the new user record, or None when the store already has users.
    The empty-store check runs inside the identity layer's write lock, so
    concurrent bootstrap requests cannot both succeed.
    """
    from traittutor.multi_user.identity import save_initial_admin

    record = save_initial_admin(
        username, hash_password(plain_password), AUTH_USERNAME, AUTH_PASSWORD_HASH
    )
    if record is not None:
        logger.info("Initial administrator bootstrapped: %s", username)
    return record


def change_password(username: str, current_password: str, new_password: str) -> bool:
    """Replace a user's password after verifying the current credential."""
    record = _load_users().get(username)
    if not record or not verify_password(current_password, str(record.get("hash") or "")):
        return False
    from traittutor.multi_user.identity import update_password_hash

    return update_password_hash(username, hash_password(new_password))


def list_users() -> list[dict]:
    """Return a list of user info dicts (username, role, created_at) — no hashes."""
    from traittutor.multi_user.identity import list_user_info

    return list_user_info(AUTH_USERNAME, AUTH_PASSWORD_HASH)


def delete_user(username: str) -> bool:
    """
    Remove a user from the store. Returns True if the user existed.

    """
    from traittutor.multi_user.identity import delete_user as _delete_user

    if not _delete_user(username):
        return False
    logger.info("User '%s' deleted", username)
    return True


def set_role(username: str, role: str) -> bool:
    """
    Change the role for an existing user. Returns True on success.

    Valid roles: 'admin', 'user'.
    """
    if role not in ("admin", "user"):
        raise ValueError(f"Invalid role: {role!r}. Must be 'admin' or 'user'.")

    from traittutor.multi_user.identity import set_role as _set_role

    if not _set_role(username, role):  # type: ignore[arg-type]
        return False
    logger.info(f"User '{username}' role updated to {role!r}")
    return True


def set_avatar(username: str, avatar: str) -> bool:
    """
    Update the avatar marker for an existing user. Returns True on success.

    The marker is either '' (deterministic fallback), 'icon:<name>:<color>',
    or 'img:<version>' (managed by the avatar upload endpoint).
    """
    from traittutor.multi_user.identity import set_avatar as _set_avatar

    if not _set_avatar(username, avatar):
        return False
    logger.info("User '%s' avatar updated", username)
    return True


def get_user_info(username: str) -> dict | None:
    """Return the public info dict for a single user, or None if unknown."""
    for item in list_users():
        if item.get("username") == username:
            return item
    return None


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------


def create_token(username: str, role: str = "user", user_id: str | None = None) -> str:
    """Create a signed JWT for the given username and role."""
    from jose import jwt

    record = _load_users().get(username) or {}
    if not user_id:
        user_id = str(record.get("id") or "")

    payload = {
        "sub": username,
        "role": role,
        "uid": user_id,
        "ver": int(record.get("token_version") or 1),
        "exp": datetime.now(timezone.utc) + timedelta(hours=TOKEN_EXPIRE_HOURS),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, AUTH_SECRET, algorithm=_ALGORITHM)


def decode_token(token: str) -> TokenPayload | None:
    """
    Validate a token and return a TokenPayload, or None if invalid.

    Tokens are validated locally against the canonical user records.
    """
    if not token:
        return None

    from jose import JWTError, jwt

    if not AUTH_SECRET:
        return None

    try:
        payload = jwt.decode(token, AUTH_SECRET, algorithms=[_ALGORITHM])
        username = payload.get("sub")
        if not username:
            return None
        record = _load_users().get(str(username))
        if not record or bool(record.get("disabled", False)):
            return None
        if int(payload.get("ver") or 1) != int(record.get("token_version") or 1):
            return None
        user_id = str(record.get("id") or payload.get("uid") or "")
        return TokenPayload(
            username=str(username), role=str(record.get("role") or "user"), user_id=user_id
        )
    except JWTError:
        return None


# ---------------------------------------------------------------------------
# Main auth entry point
# ---------------------------------------------------------------------------


def authenticate(username: str, password: str) -> TokenPayload | None:
    """
    Validate credentials. Returns a TokenPayload on success, None on failure.

    When auth is disabled, always returns a dummy admin payload so that
    callers don't need to special-case the disabled state.
    """
    if not AUTH_ENABLED:
        return TokenPayload(username=username or "local", role="admin", user_id="local-admin")

    users = _load_users()
    if not users:
        logger.warning(
            "No users configured — login will always fail. "
            "Navigate to /register to create your first account."
        )
        return None

    record = users.get(username)
    if not record:
        return None
    if bool(record.get("disabled", False)):
        return None

    hashed = record.get("hash", "") if isinstance(record, dict) else record
    if not verify_password(password, hashed):
        return None

    role = record.get("role", "user") if isinstance(record, dict) else "user"
    user_id = str(record.get("id") or "") if isinstance(record, dict) else ""
    return TokenPayload(username=username, role=role, user_id=user_id)
