"""Transactional, owner-bound SQLite primitives for unified business storage.

The initial schema is intentionally limited to migration and audit metadata.
Domain tables are introduced only with their source-specific migration, which
keeps an interrupted rollout from exposing a half-migrated aggregate.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
import os
from pathlib import Path
import shutil
import sqlite3
import threading
import time
from typing import Iterator

from traittutor.services.path_service import PathService, get_path_service

CURRENT_SCHEMA_VERSION = 1
DEFAULT_BUSY_TIMEOUT_MS = 10_000

# Domain stores share one physical SQLite database.  A callback invoked from
# inside one store's write transaction must reuse that connection when it
# writes another domain; opening a second ``BEGIN IMMEDIATE`` connection would
# wait on the lock held by the caller and eventually surface as a 500.  A tuple
# keeps ContextVar values immutable when asyncio copies a context.
_ACTIVE_TRANSACTIONS: ContextVar[tuple[tuple[Path, int, int | None, sqlite3.Connection], ...]] = (
    ContextVar("unified_store_active_transactions", default=())
)


def _execution_identity() -> tuple[int, int | None]:
    """Identify the current thread/task so copied async contexts stay safe."""
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    return threading.get_ident(), id(task) if task is not None else None


class UnifiedStoreError(RuntimeError):
    """The unified business database cannot safely complete an operation."""


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _validate_owner_id(owner_id: str) -> str:
    normalized = owner_id.strip()
    if not normalized:
        raise ValueError("owner_id is required")
    return normalized


def _configure_connection(connection: sqlite3.Connection) -> None:
    connection.row_factory = sqlite3.Row
    # Install the busy handler before requesting WAL mode.  On a fresh database
    # multiple spawned workers may reach this PRAGMA together; without the
    # handler SQLite can fail immediately instead of waiting for the peer that
    # is creating the journal files.
    connection.execute(f"PRAGMA busy_timeout = {DEFAULT_BUSY_TIMEOUT_MS}")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")


def _apply_initial_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS storage_schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL,
            checksum TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS storage_migration_runs (
            migration_id TEXT PRIMARY KEY,
            owner_id TEXT NOT NULL,
            source_kind TEXT NOT NULL,
            source_path TEXT NOT NULL,
            source_sha256 TEXT NOT NULL,
            target_schema_version INTEGER NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('started', 'completed', 'failed')),
            started_at TEXT NOT NULL,
            completed_at TEXT,
            details_json TEXT NOT NULL DEFAULT '{}',
            UNIQUE(owner_id, source_path, source_sha256, target_schema_version)
        );

        CREATE INDEX IF NOT EXISTS idx_storage_migration_runs_owner_status
        ON storage_migration_runs(owner_id, status, started_at);
        """
    )
    connection.execute(
        "INSERT OR IGNORE INTO storage_schema_migrations(version, applied_at, checksum) "
        "VALUES (?, ?, ?)",
        (CURRENT_SCHEMA_VERSION, _utc_now(), "unified-storage-initial-v1"),
    )


def initialize_database(db_path: Path) -> None:
    """Create or upgrade the foundation schema without touching legacy data."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + DEFAULT_BUSY_TIMEOUT_MS / 1000
    while True:
        connection = sqlite3.connect(db_path, timeout=DEFAULT_BUSY_TIMEOUT_MS / 1000)
        try:
            _configure_connection(connection)
            connection.execute("BEGIN IMMEDIATE")
            _apply_initial_schema(connection)
            connection.commit()
            return
        except sqlite3.OperationalError as exc:
            connection.rollback()
            # ``PRAGMA journal_mode = WAL`` can report SQLITE_BUSY immediately
            # while another process initializes the same fresh database, even
            # with a busy handler installed.  Retry only that transient class;
            # schema and filesystem errors must still fail closed.
            transient = "locked" in str(exc).lower() or "busy" in str(exc).lower()
            if not transient or time.monotonic() >= deadline:
                raise UnifiedStoreError("unable to initialize unified business database") from exc
            time.sleep(0.01)
        except sqlite3.Error as exc:
            connection.rollback()
            raise UnifiedStoreError("unable to initialize unified business database") from exc
        finally:
            connection.close()


def create_backup(db_path: Path, backup_path: Path) -> None:
    """Create a verified SQLite backup without overwriting a prior backup."""
    if backup_path.exists():
        raise UnifiedStoreError("refusing to overwrite an existing database backup")
    initialize_database(db_path)
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(db_path, timeout=DEFAULT_BUSY_TIMEOUT_MS / 1000)
    destination = sqlite3.connect(backup_path, timeout=DEFAULT_BUSY_TIMEOUT_MS / 1000)
    try:
        _configure_connection(source)
        source.backup(destination)
        destination.commit()
    except sqlite3.Error as exc:
        raise UnifiedStoreError("unable to create unified business database backup") from exc
    finally:
        destination.close()
        source.close()
    if UnifiedStore("backup-verifier", db_path=backup_path).integrity_check() != "ok":
        raise UnifiedStoreError("database backup failed integrity verification")


def restore_backup(backup_path: Path, db_path: Path) -> None:
    """Restore a verified backup only into a new explicit target path."""
    if not backup_path.is_file():
        raise UnifiedStoreError("database backup does not exist")
    if db_path.exists():
        raise UnifiedStoreError("refusing to overwrite an existing database during restore")
    if UnifiedStore("backup-verifier", db_path=backup_path).integrity_check() != "ok":
        raise UnifiedStoreError("database backup failed integrity verification")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(backup_path, db_path)
        os.chmod(db_path, 0o600)
    except OSError as exc:
        raise UnifiedStoreError("unable to restore unified business database backup") from exc


class UnifiedStore:
    """Base store that binds every query to a server-resolved owner scope.

    ``owner_id`` must originate from the authenticated server-side scope.  It
    only selects rows in the already owner-scoped database; it never controls
    the resolved database path.
    """

    def __init__(
        self,
        owner_id: str,
        *,
        path_service: PathService | None = None,
        db_path: Path | None = None,
    ) -> None:
        self.owner_id = _validate_owner_id(owner_id)
        if db_path is not None and path_service is not None:
            raise ValueError("provide either db_path or path_service, not both")
        self._db_path = (
            db_path or (path_service or get_path_service()).get_traittutor_database_path()
        )

    @property
    def db_path(self) -> Path:
        return self._db_path

    def active_transaction_connection(self) -> sqlite3.Connection | None:
        """Return this execution's same-database parent transaction, if any."""
        database = self.db_path.resolve()
        thread_id, task_id = _execution_identity()
        return next(
            (
                connection
                for path, owner_thread, owner_task, connection in reversed(
                    _ACTIVE_TRANSACTIONS.get()
                )
                if path == database and owner_thread == thread_id and owner_task == task_id
            ),
            None,
        )

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Yield one immediate transaction, reusing a same-database parent."""
        database = self.db_path.resolve()
        thread_id, task_id = _execution_identity()
        active = _ACTIVE_TRANSACTIONS.get()
        parent = self.active_transaction_connection()
        if parent is not None:
            yield parent
            return

        initialize_database(self.db_path)
        connection = sqlite3.connect(
            self.db_path, timeout=DEFAULT_BUSY_TIMEOUT_MS / 1000, isolation_level=None
        )
        token = None
        try:
            _configure_connection(connection)
            connection.execute("BEGIN IMMEDIATE")
            token = _ACTIVE_TRANSACTIONS.set((*active, (database, thread_id, task_id, connection)))
            yield connection
            connection.commit()
        except sqlite3.Error as exc:
            connection.rollback()
            raise UnifiedStoreError("unified business transaction failed") from exc
        except Exception:
            connection.rollback()
            raise
        finally:
            if token is not None:
                _ACTIVE_TRANSACTIONS.reset(token)
            connection.close()

    def integrity_check(self) -> str:
        """Return SQLite's integrity status for operational health checks."""
        initialize_database(self.db_path)
        connection = sqlite3.connect(self.db_path, timeout=DEFAULT_BUSY_TIMEOUT_MS / 1000)
        try:
            _configure_connection(connection)
            result = connection.execute("PRAGMA integrity_check").fetchone()
            return str(result[0]) if result is not None else "unknown"
        except sqlite3.Error as exc:
            raise UnifiedStoreError("unable to check unified business database") from exc
        finally:
            connection.close()
