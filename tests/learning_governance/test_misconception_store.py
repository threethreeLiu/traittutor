from __future__ import annotations

import pytest

from traittutor.learning_model.misconception import (
    MisconceptionStore,
    MisconceptionStoreError,
)

NOW = "2026-08-10T00:00:00Z"
LATER = "2026-08-10T00:01:00Z"


def _propose(store: MisconceptionStore, hypothesis_id: str = "h1") -> None:
    store.propose(
        hypothesis_id=hypothesis_id,
        user_id="user-a",
        subject_id="math",
        kc_ids=("fractions",),
        rubric_ref="server-rubric#fraction-sign",
        pattern="reverses numerator and denominator",
        evidence_refs=("event-1",),
        created_at=NOW,
    )


def test_durable_store_requires_and_enforces_owner(tmp_path) -> None:
    path = tmp_path / "misconceptions.json"
    with pytest.raises(ValueError, match="owner_id"):
        MisconceptionStore(path)

    store = MisconceptionStore(path, owner_id="user-a")
    with pytest.raises(PermissionError, match="owner"):
        store.propose(
            hypothesis_id="foreign",
            user_id="user-b",
            subject_id="math",
            kc_ids=("fractions",),
            rubric_ref="private",
            pattern="foreign",
            created_at=NOW,
        )

    _propose(store)
    with pytest.raises(MisconceptionStoreError, match="unreadable"):
        MisconceptionStore(path, owner_id="user-b")


def test_durable_store_survives_reload_and_filters_subject_and_kc(tmp_path) -> None:
    path = tmp_path / "misconceptions.json"
    _propose(MisconceptionStore(path, owner_id="user-a"))

    reopened = MisconceptionStore(path, owner_id="user-a")
    assert reopened.get("h1") is not None
    assert [
        item.hypothesis_id
        for item in reopened.list_for(user_id="user-a", subject_id="math", kc_id="fractions")
    ] == ["h1"]
    assert reopened.list_for(user_id="user-a", subject_id="physics") == []
    assert reopened.list_for(user_id="user-a", subject_id="math", kc_id="vectors") == []


def test_durable_instances_do_not_lose_distinct_evidence(tmp_path) -> None:
    path = tmp_path / "misconceptions.json"
    first = MisconceptionStore(path, owner_id="user-a")
    second = MisconceptionStore(path, owner_id="user-a")
    _propose(first)

    first.add_evidence("h1", "event-2", now=LATER)
    second.add_evidence("h1", "event-3", now=LATER)

    reloaded = MisconceptionStore(path, owner_id="user-a")
    item = reloaded.get("h1")
    assert item is not None
    assert set(item.evidence_refs) == {"event-1", "event-2", "event-3"}
