#!/usr/bin/env python3
"""Read-only reconciliation of migrated sources against the unified DB (Phase 5).

Verifies that every migrated source is faithfully represented in the unified
database (counts, primary keys, owner, verbatim payload sha) and that source
files on disk match the pre-migration archive (no reverse-overwrite).  This is
the "final hash and count verification" step of plan §5 Phase 5 item 3.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from traittutor.unified_storage import (
    DEFAULT_SOURCE_NAMES,
    reconcile_sources,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Reconcile migrated sources against the unified DB."
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="JSON file to write the reconciliation report.",
    )
    parser.add_argument(
        "--archive-root",
        type=Path,
        default=None,
        help="Pre-migration archive directory for source-integrity check.",
    )
    parser.add_argument(
        "--sources",
        type=str,
        default=",".join(DEFAULT_SOURCE_NAMES),
        help="Comma-separated source names to reconcile.",
    )
    parser.add_argument(
        "--owner-scope",
        default="default",
        help="Workspace owner label for PATH_SCOPE attribution.",
    )
    parser.add_argument(
        "--owner-id",
        default="local-admin",
        help="Server-resolved workspace owner id.",
    )
    return parser


def _dataclass(obj: object) -> object:
    """Recursively convert dataclasses and tuples to plain JSON structures."""
    if hasattr(obj, "__dataclass_fields__"):
        return {k: _dataclass(v) for k, v in obj.__dict__.items()}
    if isinstance(obj, tuple):
        return [_dataclass(i) for i in obj]
    return obj


def main() -> None:
    args = _parser().parse_args()
    report = reconcile_sources(
        source_names=tuple(s for s in args.sources.split(",") if s),
        owner_scope=args.owner_scope,
        owner_id=args.owner_id,
        archive_root=args.archive_root,
    )
    payload = _dataclass(report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"reconciliation report written: {args.output}")
    print(f"  reconciled={report.reconciled} integrity={report.integrity_check}")
    not_intact = [c for c in report.source_intact if not c.intact]
    if not_intact:
        print(f"  source integrity failures: {len(not_intact)}")
    failed = [s for s in report.sections if not s.reconciled]
    if failed:
        print(f"  failed sections: {len(failed)}")
        for s in failed:
            print(
                f"    {s.source_name}/{s.source_section}: "
                f"source={s.source_record_count} db={s.db_record_count} "
                f"missing={s.missing_in_db_count} mismatch={s.payload_mismatch_count} "
                f"extra={s.extra_in_db_count}"
            )


if __name__ == "__main__":
    main()
