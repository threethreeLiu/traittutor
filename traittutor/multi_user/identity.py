"""Canonical identity store for the optional multi-user layer."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from pathlib import Path
import secrets
import threading
from typing import Any
from uuid import uuid4

from traittutor.unified_storage import SectionedRecordStore, SQLiteDocumentStore

from .models import Role
from .paths import SYSTEM_ROOT

logger = logging.getLogger(__name__)

# Serialises writes to USERS_FILE so a concurrent burst of /register requests
# cannot all see ``not users`` and each promote themselves to admin. Single-
# process FastAPI deployments are fully covered. SQLite-backed account writes
# are serialized at their storage boundary for multi-worker deployments.
_USERS_WRITE_LOCK = threading.Lock()

AUTH_DIR = SYSTEM_ROOT / "auth"
USERS_FILE = AUTH_DIR / "users.json"


def new_user_id() -> str:
    return f"u_{uuid4().hex}"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_record(
    username: str,
    value: Any,
    *,
    default_role: Role = "user",
) -> dict[str, Any] | None:
    if isinstance(value, str):
        return {
            "id": new_user_id(),
            "hash": value,
            "role": default_role,
            "created_at": utc_now(),
            "disabled": False,
            "avatar": "",
        }
    if not isinstance(value, dict):
        return None
    hashed = str(value.get("hash") or value.get("password_hash") or "")
    if not hashed:
        return None
    role = str(value.get("role") or default_role)
    if role not in {"admin", "user"}:
        role = default_role
    return {
        "id": str(value.get("id") or new_user_id()),
        "hash": hashed,
        "role": role,
        "created_at": str(value.get("created_at") or utc_now()),
        "disabled": bool(value.get("disabled", False)),
        "token_version": int(value.get("token_version") or 1),
        "avatar": str(value.get("avatar") or ""),
    }


def _user_store() -> SectionedRecordStore:
    return SectionedRecordStore(
        "user_accounts",
        "system",
        schema_version=1,
        db_path=USERS_FILE.parent.parent / "traittutor.sqlite3",
    )


def _write_users(users: dict[str, dict[str, Any]]) -> None:
    _user_store().replace_all(
        {
            "schema_version": 1,
            "users": [{"username": username, **record} for username, record in users.items()],
        }
    )


def load_users(  # nosec B107 - empty defaults mean "no env fallback supplied".
    env_username: str = "",
    env_password_hash: str = "",
) -> dict[str, dict[str, Any]]:
    """Load canonical users and apply the configured bootstrap fallback."""
    users = {
        str(item["username"]): {key: value for key, value in item.items() if key != "username"}
        for item in _user_store().snapshot()["users"]
        if item.get("username")
    }

    canonical: dict[str, dict[str, Any]] = {}
    changed = False
    for index, (username, value) in enumerate(users.items()):
        role: Role = "admin" if index == 0 else "user"
        if isinstance(value, dict) and str(value.get("role") or "") in {"admin", "user"}:
            role = str(value.get("role"))  # type: ignore[assignment]
        record = _canonical_record(str(username), value, default_role=role)
        if record is None:
            changed = True
            continue
        canonical[str(username)] = record
        changed = changed or record != value

    if changed:
        _write_users(canonical)

    if canonical:
        return canonical

    if env_username and env_password_hash:
        return {
            env_username: {
                "id": "env-admin",
                "hash": env_password_hash,
                "role": "admin",
                "created_at": "",
                "disabled": False,
            }
        }

    return {}


def save_user(username: str, hashed_password: str, role: Role | None = None) -> dict[str, Any]:
    # Bootstrap is the only path allowed to create an administrator. Public
    # registration must never obtain privilege from timing.
    with _USERS_WRITE_LOCK:
        users = load_users()
        existing = users.get(username) or {}
        # An explicit role wins; otherwise an existing account keeps its role
        # and a brand-new account defaults to "user".
        effective_role: Role = role if role is not None else (existing.get("role") or "user")
        record = {
            "id": str(existing.get("id") or new_user_id()),
            "hash": hashed_password,
            "role": effective_role,
            "created_at": str(existing.get("created_at") or utc_now()),
            "disabled": bool(existing.get("disabled", False)),
            "token_version": int(existing.get("token_version") or 1),
            "avatar": str(existing.get("avatar") or ""),
        }
        users[username] = record
        _write_users(users)
    return record


def save_initial_admin(  # nosec B107 - empty defaults mean "no env fallback supplied".
    username: str,
    hashed_password: str,
    env_username: str = "",
    env_password_hash: str = "",
) -> dict[str, Any] | None:
    """Atomically create the first administrator, or None if users already exist.

    The empty-store check happens inside ``_USERS_WRITE_LOCK`` so concurrent
    bootstrap requests cannot both observe an empty store and each create an
    admin. The env fallback credentials are honoured the same way
    ``load_users`` honours them.
    """
    with _USERS_WRITE_LOCK:
        users = load_users(env_username, env_password_hash)
        if users:
            return None
        record = {
            "id": new_user_id(),
            "hash": hashed_password,
            "role": "admin",
            "created_at": utc_now(),
            "disabled": False,
            "token_version": 1,
            "avatar": "",
        }
        _write_users({username: record})
    return record


def list_user_info(  # nosec B107 - empty defaults mean "no env fallback supplied".
    env_username: str = "",
    env_password_hash: str = "",
) -> list[dict[str, Any]]:
    return [
        {
            "id": record.get("id", ""),
            "username": username,
            "role": record.get("role", "user"),
            "created_at": record.get("created_at", ""),
            "disabled": bool(record.get("disabled", False)),
            "avatar": str(record.get("avatar") or ""),
        }
        for username, record in load_users(env_username, env_password_hash).items()
    ]


def get_user(username: str) -> dict[str, Any] | None:
    return load_users().get(username)


def get_user_by_id(user_id: str) -> tuple[str, dict[str, Any]] | None:
    for username, record in load_users().items():
        if str(record.get("id") or "") == user_id:
            return username, record
    return None


def delete_user(username: str) -> bool:
    users = load_users()
    if username not in users:
        return False
    users.pop(username, None)
    _write_users(users)
    return True


def set_disabled(username: str, disabled: bool) -> bool:
    """Enable/disable an account and invalidate all of its sessions."""
    with _USERS_WRITE_LOCK:
        users = load_users()
        record = users.get(username)
        if record is None:
            return False
        record["disabled"] = disabled
        record["token_version"] = int(record.get("token_version") or 1) + 1
        _write_users(users)
    return True


def invalidate_sessions(username: str) -> bool:
    """Advance the account session version without changing the account."""
    with _USERS_WRITE_LOCK:
        users = load_users()
        record = users.get(username)
        if record is None:
            return False
        record["token_version"] = int(record.get("token_version") or 1) + 1
        _write_users(users)
    return True


def set_avatar(username: str, avatar: str) -> bool:
    """Update the avatar marker for an existing user. Returns True on success."""
    with _USERS_WRITE_LOCK:
        users = load_users()
        if username not in users:
            return False
        users[username]["avatar"] = avatar
        _write_users(users)
    return True


# ---------------------------------------------------------------------------
# Avatar image files — stored next to the user store, keyed by user id
# ---------------------------------------------------------------------------

# Extensions are derived from server-side content sniffing, never from the
# uploaded filename, so this list is also the full set of files we may serve.
AVATAR_EXTENSIONS = ("png", "jpg", "webp")


def _avatar_dir() -> Path:
    # Resolved lazily so tests that monkeypatch AUTH_DIR keep avatars isolated.
    return AUTH_DIR / "avatars"


def get_avatar_file(user_id: str) -> Path | None:
    """Return the stored avatar image for ``user_id``, or None."""
    for ext in AVATAR_EXTENSIONS:
        candidate = _avatar_dir() / f"{user_id}.{ext}"
        if candidate.is_file():
            return candidate
    return None


def save_avatar_file(user_id: str, data: bytes, ext: str) -> Path:
    """Atomically persist an avatar image, replacing any previous one."""
    if ext not in AVATAR_EXTENSIONS:
        raise ValueError(f"Unsupported avatar extension: {ext!r}")
    directory = _avatar_dir()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{user_id}.{ext}"
    tmp = directory / f"{user_id}.{ext}.tmp"
    tmp.write_bytes(data)
    tmp.replace(target)
    # A re-upload may change the extension; drop stale siblings.
    for other in AVATAR_EXTENSIONS:
        if other != ext:
            (directory / f"{user_id}.{other}").unlink(missing_ok=True)
    return target


def delete_avatar_file(user_id: str) -> None:
    for ext in AVATAR_EXTENSIONS:
        (_avatar_dir() / f"{user_id}.{ext}").unlink(missing_ok=True)


def set_role(username: str, role: Role) -> bool:
    if role not in {"admin", "user"}:
        raise ValueError("role must be 'admin' or 'user'")
    users = load_users()
    if username not in users:
        return False
    users[username]["role"] = role
    _write_users(users)
    return True


def update_password_hash(username: str, password_hash: str) -> bool:
    with _USERS_WRITE_LOCK:
        users = load_users()
        if username not in users:
            return False
        users[username]["hash"] = password_hash
        users[username]["token_version"] = int(users[username].get("token_version") or 1) + 1
        _write_users(users)
    return True


def load_or_create_auth_secret() -> str:
    try:
        store = SQLiteDocumentStore(
            "system",
            namespace="auth-secrets",
            db_path=SYSTEM_ROOT / "traittutor.sqlite3",
        )
        existing = str(store.load("jwt", "") or "").strip()
        if existing:
            return existing
        generated = secrets.token_hex(32)
        store.save("jwt", generated)
        logger.warning(
            "Auth is enabled and no auth secret exists. Generated a stable SQLite secret."
        )
        return generated
    except Exception as exc:
        logger.warning("Failed to load/create auth secret: %s", exc)
        return secrets.token_hex(32)
