"""Enumerate authenticated owners without accepting caller-controlled paths."""

from __future__ import annotations

from traittutor.multi_user.identity import list_user_info
from traittutor.multi_user.models import CurrentUser
from traittutor.multi_user.paths import local_admin_user, scope_for_user


def active_owner_contexts() -> tuple[CurrentUser, ...]:
    """Return each enabled canonical owner exactly once.

    Operational workers derive workspaces from the identity registry. A raw
    directory scan would let orphaned or attacker-created directories become
    an authority source, so it is deliberately not used here.
    """

    owners: list[CurrentUser] = [local_admin_user()]
    seen = {owners[0].id}
    for record in list_user_info():
        owner_id = str(record.get("id") or "").strip()
        username = str(record.get("username") or "").strip()
        role = str(record.get("role") or "user")
        if (
            not owner_id
            or not username
            or owner_id in seen
            or bool(record.get("disabled"))
            or role not in {"admin", "user"}
        ):
            continue
        owners.append(
            CurrentUser(
                id=owner_id,
                username=username,
                role=role,  # type: ignore[arg-type]
                scope=scope_for_user(owner_id, is_admin=role == "admin"),
            )
        )
        seen.add(owner_id)
    return tuple(owners)


__all__ = ["active_owner_contexts"]
