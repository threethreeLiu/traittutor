#!/usr/bin/env python
"""One-shot migration: convert every prompt YAML asset to canonical Markdown.

Walks ``traittutor/**/prompts`` and ``traittutor/**/hints`` for ``.yaml`` /
``.yml`` files, rewrites each as canonical Markdown (YAML frontmatter + ``##``
sections, see ``traittutor/services/prompt/markdown.py``), verifies the parsed
Markdown reproduces the original dict exactly, and only then deletes the YAML.

Usage:
    python scripts/migrate_prompts_to_md.py           # dry-run, reports only
    python scripts/migrate_prompts_to_md.py --apply   # write .md, delete YAML
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from traittutor.services.prompt.markdown import (  # noqa: E402
    _is_movable_text,
    dump_markdown_prompt,
    load_markdown_prompt,
    parse_markdown_prompt,
)

PACKAGE_DIR = PROJECT_ROOT / "traittutor"


def _iter_prompt_yamls() -> list[Path]:
    files: list[Path] = []
    for path in sorted(PACKAGE_DIR.rglob("*.yaml")) + sorted(PACKAGE_DIR.rglob("*.yml")):
        if "__pycache__" in path.parts:
            continue
        if "prompts" in path.parts or "hints" in path.parts:
            files.append(path)
    return files


def _flatten_prompt_structure(payload: dict) -> dict:
    """Convert the legacy ``prompt_structure`` role list into system/user keys."""
    blocks = payload.pop("prompt_structure", None)
    if not blocks:
        return payload
    system = [str(block.get("prompt", "")) for block in blocks if block.get("role") == "system"]
    user = [str(block.get("prompt", "")) for block in blocks if block.get("role") == "user"]
    payload["system"] = "\n\n".join(system)
    payload["user"] = "\n\n".join(user)
    return payload


def _normalize(data):
    """Apply the canonical normalization (strip blank lines on movable strings)."""
    if isinstance(data, dict):
        return {key: _normalize(value) for key, value in data.items()}
    if isinstance(data, list):
        return [_normalize(item) for item in data]
    if _is_movable_text(data):
        return data.strip("\n")
    return data


def migrate_file(path: Path, *, apply: bool) -> str:
    original = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(original, dict):
        return f"SKIP (not a mapping): {path}"
    transformed = _flatten_prompt_structure(dict(original))
    rendered = dump_markdown_prompt(transformed)
    reparsed = parse_markdown_prompt(rendered, source=path.name)
    if reparsed != _normalize(transformed):
        return f"FAIL (round-trip mismatch): {path}"
    target = path.with_suffix(".md")
    if apply:
        target.write_text(rendered, encoding="utf-8")
        path.unlink()
    return f"OK: {path.relative_to(PROJECT_ROOT)} -> {target.relative_to(PROJECT_ROOT)}"


def _iter_prompt_markdowns() -> list[Path]:
    files: list[Path] = []
    for path in sorted(PACKAGE_DIR.rglob("*.md")):
        if "__pycache__" in path.parts:
            continue
        if "prompts" in path.parts or "hints" in path.parts:
            files.append(path)
    return files


def rerender_file(path: Path, *, apply: bool) -> str:
    """Re-dump an existing Markdown prompt through the canonical dumper.

    Only canonical assets (frontmatter + ``##`` sections) are touched; raw
    Markdown prompt files such as capability playbooks are left alone.
    """
    if not path.read_text(encoding="utf-8").startswith("---\n"):
        return f"SKIP (raw markdown, not a canonical asset): {path}"
    data = load_markdown_prompt(path)
    rendered = dump_markdown_prompt(data)
    reparsed = parse_markdown_prompt(rendered, source=path.name)
    if reparsed != _normalize(data):
        return f"FAIL (round-trip mismatch): {path}"
    if apply:
        path.write_text(rendered, encoding="utf-8")
    return f"OK: {path.relative_to(PROJECT_ROOT)}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write .md files and delete YAML")
    parser.add_argument(
        "--rerender",
        action="store_true",
        help="re-dump existing .md prompt assets through the canonical dumper",
    )
    args = parser.parse_args()

    if args.rerender:
        files = _iter_prompt_markdowns()
        print(f"found {len(files)} prompt Markdown files")
        failures = 0
        for path in files:
            result = rerender_file(path, apply=args.apply)
            if result.startswith("FAIL"):
                print(result)
                failures += 1
        if failures:
            print(f"{failures} file(s) need manual attention")
            return 1
        print("re-render complete" if args.apply else "re-render dry-run complete")
        return 0

    files = _iter_prompt_yamls()
    print(f"found {len(files)} prompt YAML files")
    failures = 0
    for path in files:
        result = migrate_file(path, apply=args.apply)
        print(result)
        if result.startswith(("FAIL", "SKIP")):
            failures += 1
    if failures:
        print(f"{failures} file(s) need manual attention")
        return 1
    print("dry-run complete" if not args.apply else "migration applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
