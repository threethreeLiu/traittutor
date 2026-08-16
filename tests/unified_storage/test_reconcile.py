"""Tests for the Phase 5 reconciliation engine.

The reconciler is strictly read-only: these tests verify it detects drift,
missing rows, payload mismatch, extra rows, and reverse-overwrites without
mutating the database.
"""

from __future__ import annotations

from pathlib import Path

from traittutor.unified_storage import DEFAULT_SOURCE_NAMES, reconcile_sources
from traittutor.unified_storage.backup import create_source_backup
from traittutor.unified_storage.migrator import migrate_sources


def _service(tmp_path: Path):
    from traittutor.services.path_service import PathService

    return PathService(workspace_root=tmp_path)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    import json

    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_reconcile_empty_sources_reconciles(tmp_path: Path) -> None:
    service = _service(tmp_path)
    report = reconcile_sources(
        source_names=("memory_v2", "research_workspaces"),
        path_service=service,
        owner_id="local-admin",
    )
    assert report.reconciled is True
    assert report.integrity_check == "ok"
    assert len(report.sections) == 0  # absent files produce no sections


def test_reconcile_json_sectioned_matches_after_migration(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _write_json(
        service.user_data_dir / "workspace/traittutor/research_workspaces.json",
        {
            "schema_version": 1,
            "workspaces": [
                {"workspace_id": "ws-1", "owner_id": "local-admin", "name": "alpha"},
                {"workspace_id": "ws-2", "owner_id": "local-admin", "name": "beta"},
            ],
        },
    )
    migrate_sources(
        source_names=("research_workspaces",),
        path_service=service,
        owner_id="local-admin",
    )
    report = reconcile_sources(
        source_names=("research_workspaces",),
        path_service=service,
        owner_id="local-admin",
    )
    assert report.reconciled is True
    assert len(report.sections) == 9
    sec = next(item for item in report.sections if item.source_section.endswith("/workspaces"))
    assert sec.source_record_count == 2
    assert sec.db_record_count == 2
    assert sec.migrated_record_count == 2
    assert sec.missing_in_db_count == 0
    assert sec.payload_mismatch_count == 0
    assert sec.extra_in_db_count == 0
    assert sec.owner_ids == ("local-admin",)


def test_reconcile_path_scope_source_matches(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _write_json(
        service.user_data_dir / "workspace/traittutor/memory-index-v1.json",
        {
            "schema_version": 2,
            "states": [{"generation": 1, "built_at": "2026-08-01T00:00:00Z"}],
            "indexes": [{"memory_id": "m1", "generation": 1, "token": "t"}],
            "invalidations": [{"generation": 0, "reason": "superseded"}],
        },
    )
    migrate_sources(
        source_names=("memory_index",),
        path_service=service,
        owner_id="local-admin",
    )
    report = reconcile_sources(
        source_names=("memory_index",),
        path_service=service,
        owner_id="local-admin",
    )
    assert report.reconciled is True
    states = next(
        s for s in report.sections if s.source_section == "memory_index/states#path_scoped"
    )
    assert states.db_record_count == 1
    assert states.migrated_record_count == 1
    assert states.owner_ids == ("local-admin",)


def test_reconcile_detects_missing_row(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _write_json(
        service.user_data_dir / "workspace/traittutor/research_workspaces.json",
        {
            "schema_version": 1,
            "workspaces": [
                {"workspace_id": "ws-1", "owner_id": "local-admin", "name": "alpha"},
            ],
        },
    )
    migrate_sources(
        source_names=("research_workspaces",),
        path_service=service,
        owner_id="local-admin",
    )
    # Truncate the source after migration so the DB has a row the source lacks.
    _write_json(
        service.user_data_dir / "workspace/traittutor/research_workspaces.json",
        {"schema_version": 1, "workspaces": []},
    )
    report = reconcile_sources(
        source_names=("research_workspaces",),
        path_service=service,
        owner_id="local-admin",
    )
    assert report.reconciled is False
    sec = next(item for item in report.sections if item.source_section.endswith("/workspaces"))
    assert sec.extra_in_db_count == 1


def test_reconcile_detects_payload_drift(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _write_json(
        service.user_data_dir / "workspace/traittutor/research_workspaces.json",
        {
            "schema_version": 1,
            "workspaces": [{"workspace_id": "ws-1", "owner_id": "local-admin", "name": "alpha"}],
        },
    )
    migrate_sources(
        source_names=("research_workspaces",),
        path_service=service,
        owner_id="local-admin",
    )
    # Change a field in the source (not the id) — payload sha no longer matches.
    _write_json(
        service.user_data_dir / "workspace/traittutor/research_workspaces.json",
        {
            "schema_version": 1,
            "workspaces": [{"workspace_id": "ws-1", "owner_id": "local-admin", "name": "ALPHA"}],
        },
    )
    report = reconcile_sources(
        source_names=("research_workspaces",),
        path_service=service,
        owner_id="local-admin",
    )
    assert report.reconciled is False
    sec = next(item for item in report.sections if item.source_section.endswith("/workspaces"))
    assert sec.payload_mismatch_count == 1


def test_reconcile_respects_residue_files(tmp_path: Path) -> None:
    service = _service(tmp_path)
    tasks = service.user_data_dir / "workspace/traittutor/generation-tasks"
    tasks.mkdir(parents=True, exist_ok=True)
    _write_json(tasks / "g1.json", {"generation_id": "g1", "owner_id": "local-admin"})
    base = service.user_data_dir / "workspace/traittutor/generations"
    base.mkdir(parents=True, exist_ok=True)
    _write_json(base / "real.json", {"generation_id": "g1", "result": {"items": ["x"]}})
    _write_json(base / "empty.json", {"generation_id": "g2", "result": {"items": []}})
    migrate_sources(
        source_names=("generation_results",),
        path_service=service,
        owner_id="local-admin",
    )
    report = reconcile_sources(
        source_names=("generation_results",),
        path_service=service,
        owner_id="local-admin",
    )
    assert report.reconciled is True
    sec = report.sections[0]
    assert sec.source_record_count == 1
    assert sec.db_record_count == 1
    assert "1 residue file(s) excluded" in (sec.note or "")


def test_reconcile_source_intact_against_archive(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _write_json(
        service.user_data_dir / "workspace/traittutor/research_workspaces.json",
        {
            "schema_version": 1,
            "workspaces": [{"workspace_id": "ws-1", "owner_id": "local-admin", "name": "alpha"}],
        },
    )
    archive_root = tmp_path / "archive"
    create_source_backup(archive_root, path_service=service, owner_scope="local-admin")
    report = reconcile_sources(
        source_names=("research_workspaces",),
        path_service=service,
        owner_id="local-admin",
        archive_root=archive_root,
    )
    assert len(report.source_intact) == 1
    assert report.source_intact[0].intact is True


def test_reconcile_detects_reverse_overwrite(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _write_json(
        service.user_data_dir / "workspace/traittutor/research_workspaces.json",
        {
            "schema_version": 1,
            "workspaces": [{"workspace_id": "ws-1", "owner_id": "local-admin", "name": "alpha"}],
        },
    )
    archive_root = tmp_path / "archive"
    create_source_backup(archive_root, path_service=service, owner_scope="local-admin")
    # Reverse-overwrite the source after archiving.
    _write_json(
        service.user_data_dir / "workspace/traittutor/research_workspaces.json",
        {
            "schema_version": 1,
            "workspaces": [{"workspace_id": "ws-1", "owner_id": "local-admin", "name": "tampered"}],
        },
    )
    report = reconcile_sources(
        source_names=("research_workspaces",),
        path_service=service,
        owner_id="local-admin",
        archive_root=archive_root,
    )
    assert report.source_intact[0].intact is False
    assert "reverse-overwrite" in (report.source_intact[0].note or "")


def test_reconcile_sqlite_source_intact_by_integrity(tmp_path: Path) -> None:
    """SQLite archive may differ at byte level (backup API + WAL); intact check
    falls back to size + integrity_check, not raw sha."""
    service = _service(tmp_path)
    db_path = service.user_data_dir / "chat_history.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    import sqlite3

    src = sqlite3.connect(db_path)
    src.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, owner_id TEXT)")
    src.execute("INSERT INTO sessions VALUES ('s1', 'local-admin')")
    src.commit()
    src.close()

    archive_root = tmp_path / "archive"
    create_source_backup(archive_root, path_service=service, owner_scope="local-admin")
    report = reconcile_sources(
        source_names=("chat_history",),
        path_service=service,
        owner_id="local-admin",
        archive_root=archive_root,
    )
    chat = next(c for c in report.source_intact if c.source_name == "chat_history")
    assert chat.intact is True
    assert "integrity" in (chat.note or "").lower()


def test_default_source_names_covers_phases(tmp_path: Path) -> None:
    assert "capability_decisions" in DEFAULT_SOURCE_NAMES
    assert "knowledge_graph" in DEFAULT_SOURCE_NAMES
    assert "memory_index" in DEFAULT_SOURCE_NAMES
    assert "research_workspaces" in DEFAULT_SOURCE_NAMES
