"""Prompt catalog for TraitTutor's source-grounded generation flows."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping

import yaml


PROMPT_ROOT = Path(__file__).with_name("prompts")


@dataclass(frozen=True)
class PromptDefinition:
    """A versioned YAML prompt asset prepared for a generation request."""

    name: str
    path: Path
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
    blocks = payload.get("prompt_structure") or []
    system_blocks = [str(block.get("prompt", "")) for block in blocks if block.get("role") == "system"]
    user_blocks = [str(block.get("prompt", "")) for block in blocks if block.get("role") == "user"]
    if not system_blocks or not user_blocks:
        raise ValueError("prompt asset requires both system and user prompt blocks")
    return "\n\n".join(system_blocks), "\n\n".join(user_blocks)


def load_prompt(relative_path: str, variables: Mapping[str, Any]) -> PromptDefinition:
    """Load, render, and fingerprint a checked-in YAML prompt asset."""
    path = PROMPT_ROOT / relative_path
    if not path.is_file():
        raise FileNotFoundError(relative_path)
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"prompt asset {relative_path} must be a YAML mapping")
    system_prompt, user_prompt = _prompt_blocks(payload)
    schema_raw = payload.get("json_schema")
    schema = json.loads(schema_raw) if isinstance(schema_raw, str) and schema_raw.strip() else None
    source = path.read_bytes()
    return PromptDefinition(
        name=str(payload.get("name") or path.stem),
        path=path,
        system_prompt=_render(system_prompt, variables),
        user_prompt=_render(user_prompt, variables),
        json_schema=schema,
        temperature=float(payload["temperature"]) if payload.get("temperature") is not None else None,
        max_output_tokens=int(payload["max_output_tokens"]) if payload.get("max_output_tokens") else None,
        reasoning_effort=str(payload.get("reasoning_effort") or "high"),
        signature=hashlib.sha256(source).hexdigest()[:16],
    )


__all__ = ["PROMPT_ROOT", "PromptDefinition", "load_prompt"]
