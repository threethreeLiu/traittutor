from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from traittutor.services.path_service import PathService
from traittutor.unified_storage import (
    CURRENT_SCHEMA_VERSION,
    UnifiedStore,
    UnifiedStoreError,
    create_backup,
    initialize_database,
    restore_backup,
)


def test_initialization_records_schema_and_enables_integrity_check(tmp_path: Path) -> None:
    db_path = tmp_path / "traittutor.sqlite3"

    initialize_database(db_path)
    store = UnifiedStore("owner-a", db_path=db_path)

    assert store.integrity_check() == "ok"
    with sqlite3.connect(db_path) as connection:
        row = connection.execute("SELECT version FROM storage_schema_migrations").fetchone()
    assert row == (CURRENT_SCHEMA_VERSION,)
    with store.transaction() as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_transaction_rolls_back_on_error(tmp_path: Path) -> None:
    store = UnifiedStore("owner-a", db_path=tmp_path / "traittutor.sqlite3")

    with pytest.raises(UnifiedStoreError):
        with store.transaction() as connection:
            connection.execute(
                "INSERT INTO storage_migration_runs("
                "migration_id, owner_id, source_kind, source_path, source_sha256, "
                "target_schema_version, status, started_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("one", "owner-a", "json", "input.json", "abc", 1, "started", "now"),
            )
            connection.execute(
                "INSERT INTO storage_migration_runs("
                "migration_id, owner_id, source_kind, source_path, source_sha256, "
                "target_schema_version, status, started_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("one", "owner-a", "json", "other.json", "def", 1, "started", "now"),
            )

    with store.transaction() as connection:
        count = connection.execute("SELECT COUNT(*) FROM storage_migration_runs").fetchone()[0]
    assert count == 0


def test_path_is_bound_to_server_resolved_workspace(tmp_path: Path) -> None:
    service = PathService(workspace_root=tmp_path / "owner-a-root")

    store = UnifiedStore("owner-a", path_service=service)

    assert store.db_path == service.get_workspace_dir() / "traittutor" / "traittutor.sqlite3"


def test_constructor_rejects_ambiguous_database_path(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="either db_path or path_service"):
        UnifiedStore(
            "owner-a",
            path_service=PathService(workspace_root=tmp_path),
            db_path=tmp_path / "traittutor.sqlite3",
        )


def test_backup_and_restore_refuse_to_overwrite_existing_files(tmp_path: Path) -> None:
    db_path = tmp_path / "traittutor.sqlite3"
    backup_path = tmp_path / "backups" / "before-migration.sqlite3"
    restored_path = tmp_path / "restored.sqlite3"
    initialize_database(db_path)

    create_backup(db_path, backup_path)
    restore_backup(backup_path, restored_path)

    assert UnifiedStore("owner-a", db_path=restored_path).integrity_check() == "ok"
    with pytest.raises(UnifiedStoreError, match="existing database backup"):
        create_backup(db_path, backup_path)
    with pytest.raises(UnifiedStoreError, match="existing database during restore"):
        restore_backup(backup_path, restored_path)
