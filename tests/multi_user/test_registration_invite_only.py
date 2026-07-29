"""Registration is invite-only: ``save_user`` never auto-promotes to admin.

The old contract granted the first registered user the admin role, which let
any public registration win an admin race on a fresh deployment. The first
administrator is now created exclusively through the bootstrap endpoint (see
``save_initial_admin``); these tests pin the new invariants.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor


def test_first_save_user_keeps_user_role(mu_isolated_root):
    from traittutor.multi_user.identity import list_user_info, save_user

    save_user("alice", "$2b$12$placeholder")
    users = {u["username"]: u for u in list_user_info()}
    assert users["alice"]["role"] == "user"


def test_second_save_user_keeps_user_role(mu_isolated_root):
    from traittutor.multi_user.identity import list_user_info, save_user

    save_user("alice", "$2b$12$placeholder")
    save_user("bob", "$2b$12$placeholder")
    users = {u["username"]: u for u in list_user_info()}
    assert users["alice"]["role"] == "user"
    assert users["bob"]["role"] == "user"


def test_save_user_without_role_preserves_existing_role(mu_isolated_root):
    """A role-less re-save (e.g. password rotation) must not strip admin."""
    from traittutor.multi_user.identity import list_user_info, save_user

    save_user("alice", "$2b$12$placeholder", role="admin")
    save_user("alice", "$2b$12$newhash")
    users = {u["username"]: u for u in list_user_info()}
    assert users["alice"]["role"] == "admin"
    assert users["alice"]["created_at"]


def test_concurrent_save_user_never_promotes(mu_isolated_root):
    """``_USERS_WRITE_LOCK`` serialises writes, and no timing window can turn
    a concurrent first-time registration into an admin any more."""
    from traittutor.multi_user.identity import list_user_info, save_user

    def _save(name):
        try:
            save_user(name, "$2b$12$placeholder")
            return True
        except Exception:
            return False

    names = [f"u{i}" for i in range(8)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(_save, names))

    assert all(results)
    users = list_user_info()
    assert len(users) == 8
    assert [u for u in users if u["role"] == "admin"] == []


def test_save_initial_admin_only_when_store_is_empty(mu_isolated_root):
    from traittutor.multi_user.identity import list_user_info, save_initial_admin, save_user

    record = save_initial_admin("root", "$2b$12$placeholder")
    assert record is not None
    assert record["role"] == "admin"

    # A second bootstrap attempt is refused; a regular user stays "user".
    assert save_initial_admin("intruder", "$2b$12$placeholder") is None
    save_user("bob", "$2b$12$placeholder")
    users = {u["username"]: u for u in list_user_info()}
    assert set(users) == {"root", "bob"}
    assert users["root"]["role"] == "admin"
    assert users["bob"]["role"] == "user"


def test_concurrent_save_initial_admin_creates_exactly_one(mu_isolated_root):
    """The empty-store check lives inside the write lock, so a bootstrap race
    cannot mint two administrators."""
    from traittutor.multi_user.identity import list_user_info, save_initial_admin

    def _bootstrap(name):
        return save_initial_admin(name, "$2b$12$placeholder")

    names = [f"root{i}" for i in range(8)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(_bootstrap, names))

    created = [record for record in results if record is not None]
    assert len(created) == 1
    users = list_user_info()
    admins = [u for u in users if u["role"] == "admin"]
    assert len(users) == 1
    assert len(admins) == 1
    assert admins[0]["id"] == created[0]["id"]
