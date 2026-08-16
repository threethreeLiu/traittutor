"""Canonical Markdown prompt format: YAML frontmatter + ``##`` sections.

Every prompt asset in the repository uses this single format::

    ---
    # frontmatter: metadata and short values (single-line strings, numbers,
    # booleans, lists, and nested dicts whose leaves are all short values)
    name: my_prompt
    ---

    ## system

    Multi-line prompt text. Placeholders like {var} / {{var}} stay verbatim.

    ## loop.user_template

    Nested keys are addressed with dotted paths as section headings.

Move rule (shared by :func:`dump_markdown_prompt` and the migration script):

* a string leaf moves to a body section when its stripped form contains a
  newline and has no line starting with ``## ``; leading/trailing blank lines
  are normalized away (insignificant for prompt text);
* everything else stays in the frontmatter.

The parser rebuilds exactly the nested dict a plain ``yaml.safe_load`` of the
legacy asset produced, so prompt consumers need no changes.
"""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Mapping

import yaml

_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)
_SECTION_RE = re.compile(r"^## (\S[^\n]*)$", re.MULTILINE)


class PromptLoadError(RuntimeError):
    """Raised when a canonical prompt asset cannot be loaded safely.

    Prompt assets control LLM behaviour.  A malformed asset must therefore be
    visible to the caller instead of being interpreted as an empty prompt.
    """


def _is_movable_text(value: Any) -> bool:
    """Whether a string leaf must live in a body section rather than frontmatter.

    Multi-line strings move to the body; leading/trailing blank lines are
    normalized away (insignificant for prompt text), so the check applies to
    the stripped form.
    """
    if not isinstance(value, str):
        return False
    stripped = value.strip("\n")
    if not stripped or "\n" not in stripped:
        return False
    return not any(line.startswith("## ") for line in stripped.split("\n"))


def _set_nested(target: dict[str, Any], dotted_path: str, value: Any, source: str) -> None:
    parts = [part.strip() for part in dotted_path.split(".") if part.strip()]
    if not parts:
        raise PromptLoadError(f"{source}: empty section heading")
    node = target
    for part in parts[:-1]:
        existing = node.get(part)
        if existing is None:
            existing = {}
            node[part] = existing
        if not isinstance(existing, dict):
            raise PromptLoadError(
                f"{source}: section path {dotted_path!r} collides with a scalar key"
            )
        node = existing
    leaf = parts[-1]
    if leaf in node:
        raise PromptLoadError(f"{source}: duplicate key for section {dotted_path!r}")
    node[leaf] = value


def parse_markdown_prompt(text: str, *, source: str = "<prompt>") -> dict[str, Any]:
    """Parse canonical Markdown prompt text into the equivalent nested dict."""
    base: dict[str, Any] = {}
    body = text
    match = _FRONTMATTER_RE.match(text)
    if text.startswith("---") and match is None:
        raise PromptLoadError(
            f"{source}: invalid YAML frontmatter; expected a closing '---' delimiter"
        )
    if match:
        try:
            frontmatter = yaml.safe_load(match.group(1)) or {}
        except yaml.YAMLError as exc:
            raise PromptLoadError(f"{source}: invalid YAML frontmatter: {exc}") from exc
        if not isinstance(frontmatter, dict):
            raise PromptLoadError(f"{source}: frontmatter must be a mapping")
        base.update(frontmatter)
        body = text[match.end() :]

    matches = list(_SECTION_RE.finditer(body))
    for index, section in enumerate(matches):
        start = section.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        content = body[start:end].strip("\n")
        _set_nested(base, section.group(1), content, source)
    if not base:
        raise PromptLoadError(
            f"{source}: prompt asset contains no frontmatter values or prompt sections"
        )
    return base


def load_markdown_prompt(path: Path | str) -> dict[str, Any]:
    """Load a canonical Markdown prompt asset from disk."""
    path = Path(path)
    return parse_markdown_prompt(path.read_text(encoding="utf-8"), source=str(path))


def nested_prompt_text(prompts: dict[str, Any], path: tuple[str, ...], default: str = "") -> str:
    """Return the string at *path*, or *default* when it is absent or invalid."""
    value: Any = prompts
    for key in path:
        if not isinstance(value, dict):
            return default
        value = value.get(key)
    return value if isinstance(value, str) else default


def _split_mapping(
    data: Mapping[str, Any],
    prefix: tuple[str, ...],
    frontmatter: dict[str, Any],
    sections: list[tuple[str, str]],
) -> dict[str, Any]:
    kept: dict[str, Any] = {}
    for key, value in data.items():
        key_path = (*prefix, str(key))
        if _is_movable_text(value):
            sections.append((".".join(key_path), value.strip("\n")))
        elif isinstance(value, Mapping):
            nested = _split_mapping(value, key_path, frontmatter, sections)
            if nested:
                kept[key] = nested
        else:
            kept[key] = value
    if not prefix:
        frontmatter.update(kept)
    return kept


def dump_markdown_prompt(data: Mapping[str, Any]) -> str:
    """Serialize a prompt dict into canonical Markdown prompt text."""
    frontmatter: dict[str, Any] = {}
    sections: list[tuple[str, str]] = []
    _split_mapping(data, (), frontmatter, sections)

    parts: list[str] = []
    if frontmatter:
        rendered = yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False).strip()
        parts.append(f"---\n{rendered}\n---")
    for dotted_path, text in sections:
        parts.append(f"## {dotted_path}\n\n{text}")
    return "\n\n".join(parts) + "\n"


__all__ = [
    "PromptLoadError",
    "dump_markdown_prompt",
    "load_markdown_prompt",
    "nested_prompt_text",
    "parse_markdown_prompt",
]
