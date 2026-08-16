#!/usr/bin/env python3
"""Create a read-only pre-migration source backup (Phase 0).

Snapshots every registered source file into a fresh backup directory with a
verifiable manifest.  Refuses to overwrite an existing backup directory.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from traittutor.services.path_service import PathService
from traittutor.unified_storage import create_source_backup


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Back up every unified-storage source with a manifest."
    )
    parser.add_argument(
        "--backup-root",
        type=Path,
        required=True,
        help="Fresh directory to hold the backup (must not already exist).",
    )
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=None,
        help="Scope root to back up (defaults to the admin data root).",
    )
    parser.add_argument(
        "--owner-scope",
        default="local-admin",
        help="Workspace owner label recorded in the backup manifest.",
    )
    return parser


def main() -> None:
    args = _parser().parse_args()
    path_service = PathService(workspace_root=args.workspace_root) if args.workspace_root else None
    manifest = create_source_backup(
        args.backup_root, path_service=path_service, owner_scope=args.owner_scope
    )
    print(
        f"backup written: {args.backup_root}\n"
        f"  files={manifest.file_count} bytes={manifest.total_byte_size}\n"
        f"  manifest={args.backup_root / 'manifest.json'}"
    )


if __name__ == "__main__":
    main()
