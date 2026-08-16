#!/usr/bin/env python3
"""Project a baseline manifest onto target tables (Phase 0 dry-run).

Reads a baseline manifest, projects every source onto its target table, and
writes a read-only dry-run report (counts, attribution, conflicts, unmappable
sections).  Writes nothing to ``traittutor.sqlite3``.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from traittutor.services.file_io import atomic_write_json
from traittutor.unified_storage import BaselineManifest, plan_migration


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Dry-run the unified-storage migration from a baseline manifest."
    )
    parser.add_argument(
        "--manifest", type=Path, required=True, help="Baseline manifest JSON to read."
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Path to write the dry-run report JSON.",
    )
    return parser


def main() -> None:
    args = _parser().parse_args()
    manifest = BaselineManifest.model_validate_json(args.manifest.read_text("utf-8"))
    report = plan_migration(manifest)
    atomic_write_json(args.output, report.model_dump(mode="json"))
    print(report.human_summary)
    print(f"\ndry-run report written: {args.output}")


if __name__ == "__main__":
    main()
