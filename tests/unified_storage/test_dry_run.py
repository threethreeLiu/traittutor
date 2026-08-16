from __future__ import annotations

import json
from pathlib import Path

from traittutor.services.path_service import PathService
from traittutor.unified_storage import build_baseline_manifest, plan_migration


def _service(tmp_path: Path) -> PathService:
    return PathService(workspace_root=tmp_path)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _projection(report, target_table: str):
    for proj in report.target_projections:
        if proj.target_table == target_table:
            return proj
    raise AssertionError(f"no projection for {target_table}")


def test_dry_run_projects_target_tables(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _write_json(
        service.user_data_dir / "workspace/traittutor/conversations.json",
        {
            "schema_version": 1,
            "threads": [
                {"thread_id": "t1", "owner_id": "u-a"},
                {"thread_id": "t2", "owner_id": "u-a"},
            ],
            "turns": [],
            "episodes": [],
            "working_states": [],
            "open_loops": [],
            "session_bindings": [],
        },
    )
    manifest = build_baseline_manifest(path_service=service, owner_scope="test")
    report = plan_migration(manifest)

    threads = _projection(report, "conversation_threads")
    assert threads.projected_row_count == 2
    assert threads.attributed_record_count == 2
    assert threads.attribution_pending_count == 0


def test_dry_run_flags_unmappable_section(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _write_json(
        service.user_data_dir / "workspace/traittutor/conversations.json",
        {
            "schema_version": 1,
            "threads": [],
            "turns": [],
            "episodes": [],
            "working_states": [],
            "open_loops": [],
            "session_bindings": [],
            # Top-level key the registry does not map → must be reported, not dropped.
            "mystery_section": [{"x": 1}],
        },
    )
    manifest = build_baseline_manifest(path_service=service, owner_scope="test")
    report = plan_migration(manifest)

    assert any("mystery_section" in record.source_ref for record in report.unmappable_records)


def test_dry_run_needs_rebuild_for_broken_join(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _write_json(
        service.user_data_dir / "workspace/traittutor/page-schemas.json",
        {
            "schema_version": 1,
            "pages": [{"page_schema_id": "pg1", "generation_run_id": "gen_orphan"}],
        },
    )
    manifest = build_baseline_manifest(path_service=service, owner_scope="test")
    report = plan_migration(manifest)

    pages = _projection(report, "page_schemas")
    assert pages.attribution_pending_count == 1
    assert pages.needs_rebuild_count == 1


def test_dry_run_does_not_write_any_database(tmp_path: Path) -> None:
    service = _service(tmp_path)
    manifest = build_baseline_manifest(path_service=service, owner_scope="test")
    before = {p for p in tmp_path.rglob("*") if p.is_file()}

    plan_migration(manifest)

    after = {p for p in tmp_path.rglob("*") if p.is_file()}
    # The dry-run must not create any file (no sqlite3, no migration rows).
    assert after == before
    assert not (tmp_path / "user" / "workspace" / "traittutor" / "traittutor.sqlite3").exists()


def test_dry_run_human_summary_names_targets(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _write_json(
        service.user_data_dir / "workspace/traittutor/capability_decisions.json",
        {"schema_version": 1, "decisions": [], "idempotency": []},
    )
    manifest = build_baseline_manifest(path_service=service, owner_scope="test")
    report = plan_migration(manifest)

    assert "capability_decisions" in report.human_summary
    assert "target projections" in report.human_summary
