"""Prompt catalog for TraitTutor's source-grounded generation flows."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping

from traittutor.prompts import asset_path
from traittutor.services.prompt.markdown import PromptLoadError, parse_markdown_prompt

PROMPT_ROOT = asset_path("generation")


@dataclass(frozen=True)
class PromptDefinition:
    """A versioned Markdown prompt asset prepared for a generation request."""

    name: str
    path: Path | None
    system_prompt: str
    user_prompt: str
    json_schema: dict[str, Any] | None
    temperature: float | None
    max_output_tokens: int | None
    reasoning_effort: str
    signature: str


def _render(template: str, variables: Mapping[str, Any]) -> str:
    """Render explicit ``{{variable}}`` placeholders without template code."""

    def replace(match: re.Match[str]) -> str:
        key = match.group(1).strip()
        value = variables.get(key, "")
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)

    return re.sub(r"{{\s*([a-zA-Z0-9_.-]+)\s*}}", replace, template)


def _prompt_blocks(payload: Mapping[str, Any]) -> tuple[str, str]:
    system = payload.get("system")
    user = payload.get("user")
    if not isinstance(system, str) or not system.strip():
        raise PromptLoadError("prompt asset requires a system prompt block")
    if not isinstance(user, str) or not user.strip():
        raise PromptLoadError("prompt asset requires a user prompt block")
    return system, user


def load_prompt(relative_path: str, variables: Mapping[str, Any]) -> PromptDefinition:
    """Load, render, and fingerprint a checked-in Markdown prompt asset."""
    path = PROMPT_ROOT / relative_path
    if not path.is_file():
        raise PromptLoadError(f"prompt asset not found: {relative_path}")
    try:
        payload = parse_markdown_prompt(path.read_text(encoding="utf-8"), source=relative_path)
        system_prompt, user_prompt = _prompt_blocks(payload)
        schema_raw = payload.get("json_schema")
        schema = (
            json.loads(schema_raw) if isinstance(schema_raw, str) and schema_raw.strip() else None
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        if isinstance(exc, PromptLoadError):
            raise
        raise PromptLoadError(f"invalid prompt asset {relative_path}: {exc}") from exc
    source = path.read_bytes()
    return PromptDefinition(
        name=str(payload.get("name") or path.stem),
        path=path,
        system_prompt=_render(system_prompt, variables),
        user_prompt=_render(user_prompt, variables),
        json_schema=schema,
        temperature=float(payload["temperature"])
        if payload.get("temperature") is not None
        else None,
        max_output_tokens=int(payload["max_output_tokens"])
        if payload.get("max_output_tokens")
        else None,
        reasoning_effort=str(payload.get("reasoning_effort") or "high"),
        signature=hashlib.sha256(source).hexdigest()[:16],
    )


__all__ = ["PROMPT_ROOT", "PromptDefinition", "load_prompt"]
