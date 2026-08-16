from __future__ import annotations

import json
from pathlib import Path
import sqlite3

from traittutor.services.path_service import PathService
from traittutor.unified_storage import build_baseline_manifest
from traittutor.unified_storage.models import AnomalySeverity


def _service(tmp_path: Path) -> PathService:
    return PathService(workspace_root=tmp_path)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _source(manifest, name: str):
    for src in manifest.json_sources:
        if src.source_name == name:
            return src
    raise AssertionError(f"source {name} missing from manifest")


def test_inventory_counts_sectioned_json_and_resolves_owners(tmp_path: Path) -> None:
    service = _service(tmp_path)
    base = service.user_data_dir
    _write_json(
        base / "workspace/traittutor/conversations.json",
        {
            "schema_version": 1,
            "threads": [
                {"thread_id": "t1", "owner_id": "u-a"},
                {"thread_id": "t2", "owner_id": "u-a"},
            ],
            "turns": [{"turn_id": "x1", "owner_id": "u-a"}],
            "episodes": [],
            "working_states": [],
            "open_loops": [],
            "session_bindings": [],
        },
    )

    manifest = build_baseline_manifest(path_service=service, owner_scope="test")
    conversations = _source(manifest, "conversations")
    section_by_name = {s.section_name: s for s in conversations.sections}
    assert section_by_name["threads"].record_count == 2
    assert section_by_name["turns"].record_count == 1
    assert conversations.owner_resolution.resolved_owner_ids == ("u-a",)
    assert conversations.owner_resolution.unresolved_record_count == 0


def test_inventory_detects_missing_expected_source(tmp_path: Path) -> None:
    manifest = build_baseline_manifest(path_service=_service(tmp_path), owner_scope="test")
    codes = {a.code for a in manifest.anomalies}
    assert "missing_expected_source" in codes
    assert any(
        a.code == "missing_expected_source" and "misconceptions" in a.message
        for a in manifest.anomalies
    )


def test_inventory_detects_malformed_json(tmp_path: Path) -> None:
    service = _service(tmp_path)
    path = service.user_data_dir / "workspace/traittutor/conversations.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not valid json", encoding="utf-8")

    manifest = build_baseline_manifest(path_service=service, owner_scope="test")
    malformed = [a for a in manifest.anomalies if a.code == "malformed_json"]
    assert malformed and malformed[0].severity is AnomalySeverity.ERROR


def test_inventory_detects_duplicate_ids_within_section(tmp_path: Path) -> None:
    service = _service(tmp_path)
    base = service.user_data_dir
    _write_json(
        base / "workspace/traittutor/capability_decisions.json",
        {
            "schema_version": 1,
            "decisions": [
                {"decision_id": "d1", "owner_id": "u-a"},
                {"decision_id": "d1", "owner_id": "u-a"},
            ],
            "idempotency": [],
        },
    )

    manifest = build_baseline_manifest(path_service=service, owner_scope="test")
    decisions = _source(manifest, "capability_decisions")
    section = next(s for s in decisions.sections if s.section_name == "decisions")
    assert section.duplicate_id_count == 1


def test_inventory_counts_json_list_learning_packs(tmp_path: Path) -> None:
    service = _service(tmp_path)
    path = service.user_data_dir / "workspace/traittutor/learning-packs.json"
    _write_json(
        path,
        [
            {"pack_id": "p1", "owner_id": "u-a"},
            {"pack_id": "p2", "owner_id": "u-a"},
        ],
    )

    manifest = build_baseline_manifest(path_service=service, owner_scope="test")
    packs = _source(manifest, "learning_packs")
    assert packs.kind == "json_list"
    assert sum(s.record_count for s in packs.sections) == 2
    assert packs.owner_resolution.resolved_owner_ids == ("u-a",)


def test_inventory_join_resolution_flags_attribution_pending(tmp_path: Path) -> None:
    service = _service(tmp_path)
    base = service.user_data_dir
    _write_json(
        base / "workspace/traittutor/generation-tasks/gen_known.json",
        {"generation_id": "gen_known", "owner_id": "u-a"},
    )
    _write_json(
        base / "workspace/traittutor/page-schemas.json",
        {
            "schema_version": 1,
            "pages": [{"page_schema_id": "pg1", "generation_run_id": "gen_orphan"}],
        },
    )

    manifest = build_baseline_manifest(path_service=service, owner_scope="test")
    pages = _source(manifest, "page_schemas")
    assert pages.owner_resolution.unresolved_record_count == 1
    assert any(a.code == "join_resolution_failure" for a in manifest.anomalies)


def _write_system_task_sqlite(workspace_root: Path, rows: list[tuple[str, str]]) -> None:
    """Create the authoritative generation-tasks.sqlite under data/system."""
    db_path = workspace_root / "system" / "generation-tasks.sqlite"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.execute(
        "CREATE TABLE generation_tasks (generation_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL)"
    )
    con.executemany("INSERT INTO generation_tasks VALUES (?,?)", rows)
    con.commit()
    con.close()


def test_inventory_merges_authoritative_task_sqlite_for_join(tmp_path: Path) -> None:
    """A result whose owner is only in generation-tasks.sqlite still resolves."""
    service = _service(tmp_path)
    base = service.user_data_dir
    # The system sqlite carries the authoritative generation_id→owner map; no
    # legacy JSON task file exists for this id.
    _write_system_task_sqlite(tmp_path, [("gen_real", "u-a")])
    _write_json(
        base / "workspace/traittutor/generations/gen_real.json",
        {"generation_id": "gen_real", "result": {"items": [{"q": 1}]}},
    )

    manifest = build_baseline_manifest(path_service=service, owner_scope="test")
    results = next(
        c for c in manifest.per_task_collections if c.source_name == "generation_results"
    )
    assert results.owner_resolution.resolved_record_count == 1
    assert results.owner_resolution.unresolved_record_count == 0
    assert results.owner_resolution.resolved_owner_ids == ("u-a",)


def test_inventory_flags_empty_generation_residue(tmp_path: Path) -> None:
    """A never-produced generation result is residue, not attribution-pending."""
    service = _service(tmp_path)
    base = service.user_data_dir
    _write_system_task_sqlite(tmp_path, [("gen_real", "u-a")])
    _write_json(
        base / "workspace/traittutor/generations/gen_empty.json",
        # Empty result + {"items": []} shape → residue (Phase 5 cleanup target).
        {"generation_id": "gen_empty", "result": {"items": []}},
    )
    _write_json(
        base / "workspace/traittutor/generations/gen_real.json",
        {"generation_id": "gen_real", "result": {"items": [{"q": 1}]}},
    )

    manifest = build_baseline_manifest(path_service=service, owner_scope="test")
    results = next(
        c for c in manifest.per_task_collections if c.source_name == "generation_results"
    )
    assert results.residue_record_count == 1
    # The real file is attributed; the residue file is neither attributed nor pending.
    assert results.owner_resolution.resolved_record_count == 1
    assert results.owner_resolution.unresolved_record_count == 0
    assert any(a.code == "empty_residue_record" for a in manifest.anomalies)


def test_inventory_detects_owner_fragmentation(tmp_path: Path) -> None:
    service = _service(tmp_path)
    base = service.user_data_dir
    _write_json(
        base / "workspace/traittutor/tutor_personas.json",
        {
            "schema_version": 1,
            "profiles": [
                {"persona_id": "tp1", "owner_id": "u-a"},
                {"persona_id": "tp2", "owner_id": "u-b"},
            ],
            "idempotency": [],
        },
    )

    manifest = build_baseline_manifest(path_service=service, owner_scope="test")
    assert any(a.code == "owner_fragmentation" for a in manifest.anomalies)


def test_inventory_counts_per_task_collection(tmp_path: Path) -> None:
    service = _service(tmp_path)
    base = service.user_data_dir
    for gid in ("g1", "g2", "g3"):
        _write_json(
            base / f"workspace/traittutor/generation-tasks/{gid}.json",
            {"generation_id": gid, "owner_id": "u-a"},
        )

    manifest = build_baseline_manifest(path_service=service, owner_scope="test")
    tasks = next(c for c in manifest.per_task_collections if c.source_name == "generation_tasks")
    assert tasks.file_count == 3
    assert tasks.owner_resolution.resolved_record_count == 3


def test_inventory_sqlite_counts_and_owner_column(tmp_path: Path) -> None:
    service = _service(tmp_path)
    db_path = service.user_data_dir / "chat_history.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE sessions (id TEXT, owner_id TEXT)")
    conn.execute("INSERT INTO sessions VALUES ('s1', 'u-a')")
    conn.execute("INSERT INTO sessions VALUES ('s2', 'u-a')")
    conn.commit()
    conn.close()

    manifest = build_baseline_manifest(path_service=service, owner_scope="test")
    chat = next(s for s in manifest.sqlite_sources if s.source_name == "chat_history")
    assert chat.integrity_check == "ok"
    sessions = next(t for t in chat.tables if t.table_name == "sessions")
    assert sessions.row_count == 2
    assert sessions.has_owner_column is True
    assert chat.owner_resolution.resolved_owner_ids == ("u-a",)
