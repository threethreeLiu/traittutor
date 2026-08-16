from __future__ import annotations

import json
from pathlib import Path

from traittutor.services.path_service import PathService
from traittutor.unified_storage import SectionedRecordStore
from traittutor.unified_storage.legacy_aggregate_migrator import migrate_legacy_aggregates


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_split_legacy_layouts_import_idempotently(tmp_path: Path) -> None:
    ps = PathService(workspace_root=tmp_path / "data")
    workspace = ps.get_workspace_dir()
    _write(ps.get_settings_file("interface"), {"theme": "dark", "language": "en"})
    _write(
        workspace / "traittutor" / "profiles" / "p1.json",
        {"profile_id": "p1", "created_at": "2026-01-01", "scores": {}},
    )
    _write(
        workspace / "notebook" / "n1.json",
        {"id": "n1", "name": "Notebook", "created_at": 1, "updated_at": 1, "records": []},
    )
    learner = ps.get_memory_dir() / "learner"
    _write(
        learner / "global.json",
        {"owner_id": "alice", "scope": "global", "updated_at": "2026-01-01"},
    )
    _write(learner / "sessions.json", {"s1": {"trace_id": "t1"}})
    signal_path = learner / "signals" / "2026-01.jsonl"
    signal_path.parent.mkdir(parents=True, exist_ok=True)
    signal_path.write_text(
        json.dumps(
            {
                "signal_id": "sig1",
                "kind": "learner_event",
                "subject_refs": [],
                "payload": {},
                "evidence_refs": [],
                "source": "system",
                "occurred_at": "2026-01-01",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    _write(workspace / "skills" / ".tags.json", {"tags": ["style", "custom"]})
    _write(
        ps.get_knowledge_bases_root() / "kb_config.json",
        {"knowledge_bases": {"kb1": {"status": "ready"}}},
    )

    first = migrate_legacy_aggregates(path_service=ps, owner_id="alice")
    second = migrate_legacy_aggregates(path_service=ps, owner_id="alice")

    assert sum(first.values()) == 8
    assert sum(second.values()) == 0
    assert (
        SectionedRecordStore("notebooks", "alice", schema_version=1, path_service=ps).snapshot()[
            "notebooks"
        ][0]["id"]
        == "n1"
    )
    personalization = SectionedRecordStore(
        "personalization_state", "alice", schema_version=1, path_service=ps
    ).snapshot()
    assert personalization["profiles"][0]["storage_key"] == "global"
    assert personalization["signals"][0]["signal_id"] == "sig1"
    assert personalization["sessions"][0]["session_id"] == "s1"
