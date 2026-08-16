#!/usr/bin/env python3
"""Retire verified legacy business files after migration to canonical SQLite."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil

from traittutor.services.file_io import atomic_write_json
from traittutor.services.path_service import PathService


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-root", type=Path, required=True)
    parser.add_argument("--workspace-root", type=Path, default=Path("data"))
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()

    service = PathService(workspace_root=args.workspace_root)
    manifest_path = args.archive_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    verified: list[tuple[Path, Path]] = []
    for entry in manifest.get("files", []):
        source_name = str(entry.get("source_name") or "")
        if source_name in {"chat_history", "knowledge_graph"}:
            continue
        relative = Path(str(entry["relative_path"]))
        if source_name == "legacy_aggregates":
            source = service.workspace_root / relative.relative_to("_legacy_aggregates")
        else:
            source = service.user_data_dir / relative
        if not source.is_file():
            continue
        backup = args.archive_root / relative
        if not backup.is_file() or _sha256(source) != str(entry["sha256"]):
            raise SystemExit(f"source is not covered by verified backup: {source}")
        target = (
            args.archive_root / "_retired_originals" / source.relative_to(service.workspace_root)
        )
        verified.append((source, target))

    moved: list[str] = []
    if args.execute:
        for source, target in verified:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                raise SystemExit(f"refusing to overwrite retired original: {target}")
            shutil.move(source, target)
            moved.append(str(source))

    report = {
        "dry_run": not args.execute,
        "verified_count": len(verified),
        "moved_count": len(moved),
        "sources": [str(source) for source, _target in verified],
        "moved": moved,
    }
    atomic_write_json(args.archive_root / "retirement-report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
