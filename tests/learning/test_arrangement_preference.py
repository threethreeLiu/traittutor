from __future__ import annotations

from pathlib import Path

import pytest

from traittutor import learning_packs
from traittutor.services.path_service import PathService


@pytest.fixture
def learning_workspace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    service = PathService(workspace_root=tmp_path)
    monkeypatch.setattr(learning_packs, "get_path_service", lambda: service)


def test_arrangement_preference_defaults_to_none(learning_workspace: None) -> None:
    pack = learning_packs.create_pack(title="Fractions", goal="Understand fractions")
    assert pack["arrangement_preference"] is None


def test_arrangement_preference_persists_through_update(learning_workspace: None) -> None:
    pack = learning_packs.create_pack(title="Fractions", goal="Understand fractions")

    updated = learning_packs.update_pack(pack["pack_id"], {"arrangement_preference": "basic"})
    assert updated is not None
    assert updated["arrangement_preference"] == "basic"

    # The learner can switch back to auto; the value is replaceable.
    again = learning_packs.update_pack(pack["pack_id"], {"arrangement_preference": "auto"})
    assert again is not None
    assert again["arrangement_preference"] == "auto"

    # Unrelated patches must not touch the preference.
    unrelated = learning_packs.update_pack(pack["pack_id"], {"title": "Algebra"})
    assert unrelated is not None
    assert unrelated["arrangement_preference"] == "auto"
    assert unrelated["title"] == "Algebra"


def test_arrangement_preference_survives_pack_reload(learning_workspace: None) -> None:
    pack = learning_packs.create_pack(title="Fractions", goal="Understand fractions")
    learning_packs.update_pack(pack["pack_id"], {"arrangement_preference": "basic"})

    reloaded = learning_packs.get_pack(pack["pack_id"])
    assert reloaded is not None
    assert reloaded["arrangement_preference"] == "basic"
