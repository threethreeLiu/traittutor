"""Read-only multi-source backup orchestrator (Phase 0 item 4).

Snapshots every registered source file into a fresh backup directory alongside a
verifiable manifest.  The backup is strictly read-only against sources — JSON is
copied with ``shutil.copy2`` and SQLite is copied through the ``sqlite3`` backup
API opened read-only, so no ``-wal``/``-shm`` side effects touch the live data.

A backup directory may never be overwritten: if the target exists the call
raises instead of clobbering a prior snapshot (plan §5 Phase 0 item 4).
"""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
from pathlib import Path
import shutil
import sqlite3

from traittutor.services.file_io import atomic_write_json
from traittutor.services.path_service import PathService, get_path_service

from .legacy_aggregate_migrator import legacy_aggregate_source_paths
from .mapping import SOURCE_SPECS, SourceSpec
from .models import BackupFileEntry, BackupManifest

_CHUNK = 65536


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_json(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def _copy_sqlite(source: Path, target: Path) -> None:
    """Copy a live SQLite DB consistently from a read-only source connection.

    Using the backup API (rather than a raw file copy) folds any WAL contents
    into the snapshot and avoids creating ``-wal``/``-shm`` files in the source
    directory.  The source is opened ``mode=ro`` so the live data is untouched.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()
    source_uri = f"file:{source}?mode=ro"
    source_conn = sqlite3.connect(source_uri, uri=True)
    dest_conn = sqlite3.connect(target)
    try:
        source_conn.backup(dest_conn)
        dest_conn.commit()
    finally:
        dest_conn.close()
        source_conn.close()


def _source_file_paths(spec: SourceSpec, base: Path) -> list[Path]:
    if spec.kind == "json_per_task_file":
        return sorted(base.glob(spec.relative_path))
    path = base / spec.relative_path
    return [path] if path.is_file() else []


def create_source_backup(
    backup_root: Path,
    *,
    path_service: PathService | None = None,
    owner_scope: str = "default",
) -> BackupManifest:
    """Snapshot every registered source into ``backup_root`` and return a manifest.

    Raises :class:`FileExistsError` if ``backup_root`` already exists — backups
    are never overwritten.  Missing source files are skipped silently (the
    baseline manifest already reports them as anomalies); only present files are
    captured, so an empty backup root is itself a signal.
    """
    service = path_service or get_path_service()
    base = service.user_data_dir

    if backup_root.exists():
        raise FileExistsError(f"refusing to overwrite an existing source backup: {backup_root}")
    backup_root.mkdir(parents=True, exist_ok=False)

    entries: list[BackupFileEntry] = []
    total_bytes = 0

    for spec in SOURCE_SPECS:
        for source_path in _source_file_paths(spec, base):
            rel = source_path.relative_to(base)
            target = backup_root / rel
            if spec.kind == "sqlite":
                _copy_sqlite(source_path, target)
            else:
                _copy_json(source_path, target)
            size = target.stat().st_size
            entries.append(
                BackupFileEntry(
                    source_name=spec.name,
                    relative_path=str(rel),
                    sha256=_sha256_file(target),
                    byte_size=size,
                )
            )
            total_bytes += size

    registered = {entry.relative_path for entry in entries}
    for source_path in legacy_aggregate_source_paths(service):
        source_rel = source_path.relative_to(service.workspace_root)
        rel = Path("_legacy_aggregates") / source_rel
        if (
            str(source_path.relative_to(base)) in registered
            if source_path.is_relative_to(base)
            else False
        ):
            continue
        target = backup_root / rel
        _copy_json(source_path, target)
        size = target.stat().st_size
        entries.append(
            BackupFileEntry(
                source_name="legacy_aggregates",
                relative_path=str(rel),
                sha256=_sha256_file(target),
                byte_size=size,
            )
        )
        total_bytes += size

    manifest = BackupManifest(
        created_at=_utc_now(),
        owner_scope=owner_scope,
        source_root=str(base),
        backup_root=str(backup_root),
        files=tuple(entries),
        file_count=len(entries),
        total_byte_size=total_bytes,
    )
    atomic_write_json(backup_root / "manifest.json", manifest.model_dump(mode="json"))
    return manifest
