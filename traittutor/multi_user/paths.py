"""Path resolution for admin-local and per-user workspaces.

Everything lives under ``<runtime-home>/data`` so a deployment has exactly
one tree to mount and back up:

* ``data/user``           — the admin workspace (admin scope root is ``data/``)
* ``data/users/<uid>``    — one workspace per non-admin user
* ``data/system``         — deployment state: accounts, grants, audit. Never
  mounted into the sandbox runner — see ``compose.yaml``.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from traittutor.runtime.home import get_runtime_home
from traittutor.services.path_service import PathService

from .models import LOCAL_ADMIN_ID, LOCAL_ADMIN_USERNAME, CurrentUser, UserScope

PROJECT_ROOT = get_runtime_home()
ADMIN_WORKSPACE_ROOT = PROJECT_ROOT / "data"
USERS_ROOT = ADMIN_WORKSPACE_ROOT / "users"
SYSTEM_ROOT = ADMIN_WORKSPACE_ROOT / "system"

_path_services: dict[str, PathService] = {}


def admin_scope() -> UserScope:
    return UserScope(kind="admin", user_id=LOCAL_ADMIN_ID, root=ADMIN_WORKSPACE_ROOT.resolve())


def local_admin_user() -> CurrentUser:
    return CurrentUser(
        id=LOCAL_ADMIN_ID,
        username=LOCAL_ADMIN_USERNAME,
        role="admin",
        scope=admin_scope(),
    )


def scope_for_user(user_id: str, *, is_admin: bool) -> UserScope:
    if is_admin:
        return admin_scope()
    return UserScope(kind="user", user_id=user_id, root=(USERS_ROOT / user_id).resolve())


def ensure_user_workspace(user_id: str) -> Path:
    return ensure_scope_workspace(scope_for_user(user_id, is_admin=False))


def ensure_scope_workspace(scope: UserScope) -> Path:
    """Create the workspace tree for *scope* at its own root.

    Resolving from ``scope.root`` (instead of recomputing ``USERS_ROOT /
    user_id``) keeps the path authority inside the validated scope object.
    """
    root = scope.root.resolve()
    # Domain stores create their own parents on first write. Keeping scope
    # initialization to one owner root avoids a forest of empty settings,
    # knowledge, memory, chat, and book directories for every account.
    root.mkdir(parents=True, exist_ok=True)
    return root


def ensure_system_dirs() -> None:
    for child in ("auth", "grants", "audit", "indexes"):
        (SYSTEM_ROOT / child).mkdir(parents=True, exist_ok=True)


def get_path_service_for_scope(scope: UserScope) -> PathService:
    key = scope.cache_key
    service = _path_services.get(key)
    if service is None:
        service = PathService(workspace_root=scope.root)
        _path_services[key] = service
    return service


def get_admin_path_service() -> PathService:
    return get_path_service_for_scope(admin_scope())


def get_current_path_service() -> PathService:
    from .context import get_current_user_or_none

    user = get_current_user_or_none()
    if user is None:
        return PathService.get_instance()
    if user.scope.kind == "user":
        ensure_scope_workspace(user.scope)
    return get_path_service_for_scope(user.scope)


@contextmanager
def user_context(user: CurrentUser) -> Iterator[None]:
    from .context import reset_current_user, set_current_user

    token = set_current_user(user)
    try:
        yield
    finally:
        reset_current_user(token)
