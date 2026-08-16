#!/usr/bin/env python3
"""Directed cleanup of historical generation residue (Phase 5, item 4).

Targets:
* ``generation_results`` files whose ``result`` field is empty/null/``{"items": []}``
  — never-produced artifacts (349 files observed in the real workspace).
* Optionally, the deferred orphan rows left by broken generation-task joins
  (page-schemas / generation-results whose owner cannot be resolved).  These are
  **not** deleted by default; the operator must opt in.

Every deletion is audited into the unified database and a JSON cleanup report.
Sources are removed, never reverse-overwritten.  The Phase 5 archive must
already exist; this script refuses to run without it.

Default mode is dry-run; pass ``--execute`` to actually delete files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

# Allow running as a plain script (e.g. from tests via subprocess) without an
# editable install: the script lives in scripts/, so the project root is the
# parent directory and is not automatically on sys.path.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from traittutor.services.path_service import PathService, get_path_service
from traittutor.unified_storage import (
    PHASE_2_SOURCE_NAMES,
    PHASE_3_SOURCE_NAMES,
    PHASE_4_SOURCE_NAMES,
    SOURCE_SPECS,
)
from traittutor.unified_storage.inventory import _is_residue, _load_json
from traittutor.unified_storage.store import UnifiedStore, _utc_now, initialize_database

CLEANUP_SCHEMA = """
CREATE TABLE IF NOT EXISTS storage_cleanup_runs (
    cleanup_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    dry_run INTEGER NOT NULL CHECK(dry_run IN (0, 1)),
    residue_files_found INTEGER NOT NULL,
    residue_files_deleted INTEGER NOT NULL,
    orphan_rows_deleted INTEGER NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}'
);
"""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Directed cleanup of generation residue and deferred orphans."
    )
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=None,
        help="Workspace root directory (defaults to data/ under cwd).",
    )
    parser.add_argument(
        "--archive-root",
        type=Path,
        required=True,
        help="Phase-5 archive directory that holds the pre-cleanup snapshot.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/storage_cleanup_report.json"),
        help="JSON file to write the cleanup report.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually delete files; without this, only a dry-run report is produced.",
    )
    parser.add_argument(
        "--owner-id",
        default="local-admin",
        help="Server-resolved workspace owner id for the audit record.",
    )
    parser.add_argument(
        "--delete-orphan-db-rows",
        action="store_true",
        help=(
            "Also delete deferred orphan rows from the unified DB "
            "(page-schemas / generation-results with unresolved owner). "
            "Default: leave them for a separate human decision."
        ),
    )
    return parser


def _residue_files(base: Path, spec) -> list[Path]:
    files = sorted(base.glob(spec.relative_path))
    residue: list[Path] = []
    for path in files:
        payload, parse_error = _load_json(path)
        if parse_error is not None or not isinstance(payload, dict):
            continue
        if _is_residue(payload, spec.residue_if_empty_fields):
            residue.append(path)
    return residue


def _delete_residue_files(paths: list[Path]) -> list[Path]:
    deleted: list[Path] = []
    for path in paths:
        try:
            path.unlink()
            deleted.append(path)
        except OSError:
            pass
    return deleted


def _delete_orphan_rows(connection, source_names: tuple[str, ...]) -> int:
    """Delete rows whose source_section belongs to a source but owner_id is NULL.

    This is opt-in.  The orphan rows are the 2 deferred page-schemas and 1
    deferred generation-result observed in the real workspace; their owner join
    is genuinely broken, so they cannot be attributed safely.
    """
    total = 0
    for name in source_names:
        spec = next((s for s in SOURCE_SPECS if s.name == name), None)
        if spec is None:
            continue
        if spec.kind == "sqlite":
            continue
        targets = {s.target_table for s in spec.sections}
        for table in targets:
            try:
                cur = connection.execute(
                    f"DELETE FROM {table} WHERE owner_id IS NULL "  # noqa: S608
                    "AND source_section LIKE ?",
                    (f"{name}/%",),
                )
                total += cur.rowcount
            except Exception:  # noqa: BLE001 - table may not exist
                continue
    return total


def main() -> None:
    args = _parser().parse_args()
    if not args.archive_root.is_dir():
        raise SystemExit(f"archive root does not exist: {args.archive_root}")
    manifest = args.archive_root / "manifest.json"
    if not manifest.is_file():
        raise SystemExit(f"archive manifest missing: {manifest}")

    service = (
        PathService(workspace_root=args.workspace_root)
        if args.workspace_root
        else get_path_service()
    )
    base = service.user_data_dir
    db_path = service.get_traittutor_database_path()
    initialize_database(db_path)

    started_at = _utc_now()
    cleanup_id = hashlib.sha256(
        f"{started_at}:{args.execute}:{args.delete_orphan_db_rows}".encode("utf-8")
    ).hexdigest()[:16]

    generation_results = next(s for s in SOURCE_SPECS if s.name == "generation_results")
    residue = _residue_files(base, generation_results)

    deleted_paths: list[Path] = []
    if args.execute:
        deleted_paths = _delete_residue_files(residue)

    orphan_rows_deleted = 0
    store = UnifiedStore(args.owner_id, path_service=service)
    if args.execute and args.delete_orphan_db_rows:
        with store.transaction() as connection:
            connection.executescript(CLEANUP_SCHEMA)
            orphan_rows_deleted = _delete_orphan_rows(
                connection,
                PHASE_2_SOURCE_NAMES + PHASE_3_SOURCE_NAMES + PHASE_4_SOURCE_NAMES,
            )

    report = {
        "cleanup_id": cleanup_id,
        "generated_at": _utc_now(),
        "dry_run": not args.execute,
        "archive_root": str(args.archive_root),
        "owner_id": args.owner_id,
        "residue_files_found": len(residue),
        "residue_files_deleted": len(deleted_paths),
        "residue_files": [str(p.relative_to(base)) for p in residue],
        "deleted_files": [str(p.relative_to(base)) for p in deleted_paths],
        "orphan_rows_deleted": orphan_rows_deleted,
    }

    if args.execute:
        with store.transaction() as connection:
            connection.executescript(CLEANUP_SCHEMA)
            connection.execute(
                "INSERT INTO storage_cleanup_runs("
                "cleanup_id, owner_id, started_at, completed_at, dry_run, "
                "residue_files_found, residue_files_deleted, orphan_rows_deleted, details_json) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    cleanup_id,
                    args.owner_id,
                    started_at,
                    _utc_now(),
                    1 if not args.execute else 0,
                    len(residue),
                    len(deleted_paths),
                    orphan_rows_deleted,
                    json.dumps(
                        {
                            "archive_root": str(args.archive_root),
                            "deleted_files": report["deleted_files"],
                        },
                        sort_keys=True,
                    ),
                ),
            )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"cleanup report written: {args.output}")
    print(f"  dry_run={not args.execute} residue_found={len(residue)} deleted={len(deleted_paths)}")
    if args.delete_orphan_db_rows:
        print(f"  orphan_rows_deleted={orphan_rows_deleted}")
    if not args.execute:
        print("  (pass --execute to actually delete)")


if __name__ == "__main__":
    main()
