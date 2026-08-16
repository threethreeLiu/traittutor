#!/usr/bin/env python3
"""CLI: migrate Phase-2 (and later) sources into the unified SQLite store.

Runs :func:`traittutor.unified_storage.migrator.migrate_sources` and writes the
structured report to ``--output``.  Idempotent: re-running with unchanged source
files inserts zero new rows (each source is guarded by its sha256).

Always back up sources first (``scripts/storage_backup.py``); the migrator only
writes to ``traittutor.sqlite3`` and never touches legacy source files.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from traittutor.services.file_io import atomic_write_json
from traittutor.services.path_service import PathService
from traittutor.unified_storage.legacy_aggregate_migrator import migrate_legacy_aggregates
from traittutor.unified_storage.migrator import PHASE_2_SOURCE_NAMES, migrate_sources


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        required=True,
        help="path to write the JSON migration report",
    )
    parser.add_argument(
        "--sources",
        default=",".join(PHASE_2_SOURCE_NAMES),
        help="comma-separated source names to migrate (default: Phase 2 set)",
    )
    parser.add_argument("--owner-scope", default="default")
    parser.add_argument("--owner-id", default="local-admin")
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=None,
        help="Scope root to migrate (defaults to the admin data root).",
    )
    args = parser.parse_args()

    source_names = tuple(s.strip() for s in args.sources.split(",") if s.strip())
    path_service = PathService(workspace_root=args.workspace_root) if args.workspace_root else None
    report = migrate_sources(
        source_names=source_names,
        path_service=path_service,
        owner_scope=args.owner_scope,
        owner_id=args.owner_id,
    )
    legacy_aggregate_counts = migrate_legacy_aggregates(
        path_service=path_service, owner_id=args.owner_id
    )
    payload = _report_to_dict(report)
    payload["legacy_aggregate_inserted"] = legacy_aggregate_counts
    atomic_write_json(args.output, payload)

    print(f"migration report written: {args.output}")
    print(f"  inserted={report.total_inserted} deferred={report.total_deferred}")
    print(f"  reconciled={report.reconciled} integrity={report.integrity_check}")
    if report.warnings:
        print(f"  warnings={len(report.warnings)}")
    if not report.reconciled:
        print("  WARNING: not reconciled — source vs table counts differ", file=sys.stderr)
        return 2
    return 0


def _report_to_dict(report) -> dict:
    return {
        "started_at": report.started_at,
        "completed_at": report.completed_at,
        "owner_scope": report.owner_scope,
        "database_path": report.database_path,
        "reconciled": report.reconciled,
        "integrity_check": report.integrity_check,
        "total_inserted": report.total_inserted,
        "total_deferred": report.total_deferred,
        "migration_ids": list(report.migration_ids),
        "warnings": list(report.warnings),
        "results": [_result_to_dict(r) for r in report.results],
    }


def _result_to_dict(r) -> dict:
    return {
        "target_table": r.target_table,
        "source_name": r.source_name,
        "source_ref": r.source_ref,
        "source_record_count": r.source_record_count,
        "migrated_record_count": r.migrated_record_count,
        "inserted_record_count": r.inserted_record_count,
        "deferred_record_count": r.deferred_record_count,
        "residue_record_count": r.residue_record_count,
        "owner_ids": list(r.owner_ids),
        "note": r.note,
    }


if __name__ == "__main__":
    raise SystemExit(main())
