from __future__ import annotations

import pytest

from traittutor.services.evolution import EvidenceRef, Trail, TrailStore
from traittutor.unified_storage import SectionedRecordStore


def test_trail_store_appends_owner_and_subject_scoped_evidence(tmp_path) -> None:
    path = tmp_path / "events.jsonl"
    trail = Trail(
        kind="goal",
        payload={"text": "master fractions"},
        evidence=(EvidenceRef(ref_id="turn:1", source="chat"),),
        owner_id="alice",
        subject_id="math",
    )

    TrailStore("alice", path=path).append(trail)

    row = SectionedRecordStore(
        "evolution_trails", "alice", schema_version=1, legacy_path=path
    ).snapshot()["events"][0]
    assert row["owner_id"] == "alice"
    assert row["subject_id"] == "math"
    assert row["evidence"][0]["ref_id"] == "turn:1"


def test_trail_store_rejects_cross_owner_write(tmp_path) -> None:
    trail = Trail(
        kind="preference",
        payload={"text": "concise"},
        evidence=(EvidenceRef(ref_id="turn:2", source="user"),),
        owner_id="bob",
    )

    with pytest.raises(PermissionError):
        TrailStore("alice", path=tmp_path / "events.jsonl").append(trail)
