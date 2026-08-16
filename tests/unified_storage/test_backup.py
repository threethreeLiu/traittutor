from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from traittutor.services.path_service import PathService
from traittutor.unified_storage import create_source_backup


def _service(tmp_path: Path) -> PathService:
    return PathService(workspace_root=tmp_path)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_backup_snapshots_sources_with_manifest(tmp_path: Path) -> None:
    service = _service(tmp_path)
    base = service.user_data_dir
    _write_json(
        base / "workspace/traittutor/conversations.json",
        {"schema_version": 1, "threads": [{"thread_id": "t1", "owner_id": "u-a"}]},
    )
    db_path = base / "chat_history.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE sessions (id TEXT, owner_id TEXT)")
    conn.execute("INSERT INTO sessions VALUES ('s1', 'u-a')")
    conn.commit()
    conn.close()

    backup_root = tmp_path / "backup"
    manifest = create_source_backup(backup_root, path_service=service, owner_scope="test")

    assert manifest.file_count >= 2
    assert (backup_root / "manifest.json").is_file()
    copied_conv = backup_root / "workspace/traittutor/conversations.json"
    assert copied_conv.is_file()
    assert _sha(copied_conv) == _sha(base / "workspace/traittutor/conversations.json")
    copied_db = backup_root / "chat_history.db"
    assert copied_db.is_file()
    check = sqlite3.connect(f"file:{copied_db}?mode=ro", uri=True)
    assert check.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1
    check.close()


def test_backup_refuses_to_overwrite_existing_directory(tmp_path: Path) -> None:
    service = _service(tmp_path)
    backup_root = tmp_path / "backup"
    backup_root.mkdir()

    with pytest.raises(FileExistsError):
        create_source_backup(backup_root, path_service=service, owner_scope="test")


def test_backup_is_read_only_against_sources(tmp_path: Path) -> None:
    service = _service(tmp_path)
    conv = service.user_data_dir / "workspace/traittutor/conversations.json"
    _write_json(
        conv,
        {"schema_version": 1, "threads": [{"thread_id": "t1", "owner_id": "u-a"}]},
    )
    before_sha = _sha(conv)
    before_mtime = conv.stat().st_mtime_ns

    create_source_backup(tmp_path / "backup", path_service=service, owner_scope="test")

    assert _sha(conv) == before_sha
    assert conv.stat().st_mtime_ns == before_mtime


def test_backup_includes_split_legacy_business_sources(tmp_path: Path) -> None:
    service = _service(tmp_path)
    profile = service.get_workspace_dir() / "traittutor/profiles/p1.json"
    signals = service.get_memory_dir() / "learner/signals/2026-08.jsonl"
    _write_json(profile, {"profile_id": "p1"})
    signals.parent.mkdir(parents=True, exist_ok=True)
    signals.write_text('{"signal_id":"s1"}\n', encoding="utf-8")

    manifest = create_source_backup(tmp_path / "backup", path_service=service, owner_scope="test")

    backed_up = {entry.relative_path for entry in manifest.files}
    assert (
        str(Path("_legacy_aggregates") / profile.relative_to(service.workspace_root)) in backed_up
    )
    assert (
        str(Path("_legacy_aggregates") / signals.relative_to(service.workspace_root)) in backed_up
    )
