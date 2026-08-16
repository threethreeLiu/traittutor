from traittutor.multi_user.models import LOCAL_ADMIN_ID
from traittutor.operations import owners


def test_active_owner_contexts_uses_registry_and_skips_disabled_or_duplicate(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        owners,
        "list_user_info",
        lambda: [
            {"id": "alice", "username": "alice", "role": "user", "disabled": False},
            {"id": "disabled", "username": "disabled", "role": "user", "disabled": True},
            {"id": "alice", "username": "duplicate", "role": "user", "disabled": False},
            {"id": "", "username": "invalid", "role": "user", "disabled": False},
        ],
    )

    result = owners.active_owner_contexts()

    assert [owner.id for owner in result] == [LOCAL_ADMIN_ID, "alice"]
