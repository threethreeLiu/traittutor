#!/usr/bin/env python3
"""Build the unified-storage source baseline manifest (Phase 0).

Reads every registered source read-only, computes SHA-256 / counts / owner
resolution / anomalies, and writes a machine-readable manifest for the dry-run
planner.  Run before any migration: ``scripts/storage_baseline.py --output …``.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from traittutor.services.file_io import atomic_write_json
from traittutor.unified_storage import build_baseline_manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inventory all unified-storage sources into a baseline manifest."
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Path to write the baseline manifest JSON.",
    )
    parser.add_argument(
        "--owner-scope",
        default="local-admin",
        help="Workspace owner label recorded in the manifest (metadata only).",
    )
    return parser


def main() -> None:
    args = _parser().parse_args()
    manifest = build_baseline_manifest(owner_scope=args.owner_scope)
    atomic_write_json(args.output, manifest.model_dump(mode="json"))
    summary = manifest.summary
    print(
        f"baseline written: {args.output}\n"
        f"  sources={summary.total_sources} records={summary.total_records}\n"
        f"  anomalies={summary.total_anomalies} "
        f"(error={summary.error_anomaly_count} "
        f"warning={summary.warning_anomaly_count} "
        f"info={summary.info_anomaly_count})\n"
        f"  unresolved_owner_records={summary.unresolved_owner_record_count}\n"
        f"  distinct_owner_ids={list(summary.distinct_owner_ids)}"
    )


if __name__ == "__main__":
    main()
