"""Tests for the Phase 5 cleanup script.

The cleanup script deletes generation-result residue files and audits the run
into the unified DB.  Tests use ``tmp_path`` and verify both dry-run and
execute modes.
"""

from __future__ import annotations

import json
from pathlib import Path

from traittutor.unified_storage.backup import create_source_backup


def _service(tmp_path: Path):
    from traittutor.services.path_service import PathService

    return PathService(workspace_root=tmp_path)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _run_cleanup(archive_root: Path, *, execute: bool = False) -> dict:
    import subprocess
    import sys

    workspace_root = archive_root.parent
    cmd = [
        sys.executable,
        "scripts/storage_cleanup.py",
        "--workspace-root",
        str(workspace_root),
        "--archive-root",
        str(archive_root),
        "--output",
        str(archive_root.parent / "cleanup_report.json"),
    ]
    if execute:
        cmd.append("--execute")
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    return json.loads((archive_root.parent / "cleanup_report.json").read_text(encoding="utf-8"))


def test_cleanup_dry_run_counts_residue(tmp_path: Path) -> None:
    service = _service(tmp_path)
    base = service.user_data_dir / "workspace/traittutor/generations"
    base.mkdir(parents=True, exist_ok=True)
    _write_json(
        base / "real.json", {"generation_id": "g1", "owner_id": "o1", "result": {"items": ["x"]}}
    )
    _write_json(
        base / "empty.json", {"generation_id": "g2", "owner_id": "o1", "result": {"items": []}}
    )
    _write_json(base / "null.json", {"generation_id": "g3", "owner_id": "o1", "result": None})
    archive = tmp_path / "archive"
    create_source_backup(archive, path_service=service, owner_scope="local-admin")

    report = _run_cleanup(archive)
    assert report["dry_run"] is True
    assert report["residue_files_found"] == 2
    assert report["residue_files_deleted"] == 0
    assert (base / "empty.json").is_file()


def test_cleanup_execute_deletes_residue_and_audits(tmp_path: Path) -> None:
    service = _service(tmp_path)
    base = service.user_data_dir / "workspace/traittutor/generations"
    base.mkdir(parents=True, exist_ok=True)
    _write_json(
        base / "real.json", {"generation_id": "g1", "owner_id": "o1", "result": {"items": ["x"]}}
    )
    _write_json(
        base / "empty.json", {"generation_id": "g2", "owner_id": "o1", "result": {"items": []}}
    )
    archive = tmp_path / "archive"
    create_source_backup(archive, path_service=service, owner_scope="local-admin")

    report = _run_cleanup(archive, execute=True)
    assert report["dry_run"] is False
    assert report["residue_files_found"] == 1
    assert report["residue_files_deleted"] == 1
    assert not (base / "empty.json").exists()
    assert (base / "real.json").exists()

    # Audit row written.
    db = service.get_traittutor_database_path()
    import sqlite3

    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM storage_cleanup_runs").fetchone()
    assert row is not None
    assert row["residue_files_found"] == 1
    assert row["residue_files_deleted"] == 1
    assert row["dry_run"] == 0
    con.close()


def test_cleanup_refuses_without_archive(tmp_path: Path) -> None:
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "scripts/storage_cleanup.py",
            "--workspace-root",
            str(tmp_path),
            "--archive-root",
            str(tmp_path / "missing"),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "archive root does not exist" in result.stderr
