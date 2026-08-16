"""Source-grounded podcast narration for the optional audio component."""

from __future__ import annotations

from dataclasses import asdict
import json
from typing import Any, Awaitable, Callable, Mapping

from .catalog import load_prompt
from .runner import LLMRunMetadata, run_structured_prompt

StructuredRunner = Callable[..., Awaitable[tuple[dict[str, Any], LLMRunMetadata]]]

_SPEAKERS = ("host", "guest")
_MIN_TURNS = 4
_MAX_TURNS = 16
_MAX_TURN_CHARS = 500
_MAX_TITLE_CHARS = 180
_MAX_SCRIPT_CHARS = 4000


def _plain_text(value: object) -> str:
    """Collapse markdown noise and whitespace from already-validated text."""
    import re

    text = re.sub(r"[`*_#>-]+", " ", str(value or ""))
    return re.sub(r"\s+", " ", text).strip()


def _dialogue_to_script(dialogue: list[dict[str, str]]) -> str:
    """Flatten a structured dialogue into ``"speaker: text"`` lines for display."""
    lines = [f"{turn['speaker']}: {turn['text']}" for turn in dialogue]
    script = "\n\n".join(lines)
    return script[:_MAX_SCRIPT_CHARS]


def fallback_podcast_script(lesson: Mapping[str, Any]) -> str:
    """Build a bounded narration from already-validated lesson text only."""
    parts = [_plain_text(lesson.get("lesson_goal") or lesson.get("title"))]
    for section in lesson.get("sections", []):
        if not isinstance(section, Mapping):
            continue
        parts.extend(
            (
                _plain_text(section.get("section_title")),
                _plain_text(section.get("core_content")),
                _plain_text(section.get("reflection_prompt")),
            )
        )
    takeaways = lesson.get("final_takeaways") or []
    if isinstance(takeaways, list):
        parts.extend(_plain_text(item) for item in takeaways)
    return "\n\n".join(part for part in parts if part)[:_MAX_SCRIPT_CHARS]


def _validate(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and normalize the LLM payload ``{title, dialogue}``."""
    if set(value) != {"title", "dialogue"}:
        raise ValueError("podcast narration requires only title and dialogue")
    title = value.get("title")
    if not isinstance(title, str) or not title.strip() or len(title) > _MAX_TITLE_CHARS:
        raise ValueError("podcast title is invalid")
    raw_dialogue = value.get("dialogue")
    if not isinstance(raw_dialogue, list):
        raise ValueError("podcast dialogue must be an array")
    if not (_MIN_TURNS <= len(raw_dialogue) <= _MAX_TURNS):
        raise ValueError(f"podcast dialogue must have between {_MIN_TURNS} and {_MAX_TURNS} turns")
    cleaned: list[dict[str, str]] = []
    for index, turn in enumerate(raw_dialogue):
        if not isinstance(turn, Mapping):
            raise ValueError(f"podcast dialogue turn {index} must be an object")
        if set(turn) != {"speaker", "text"}:
            raise ValueError(f"podcast dialogue turn {index} may only have speaker and text")
        speaker = str(turn.get("speaker", "")).strip()
        if speaker not in _SPEAKERS:
            raise ValueError(
                f"podcast dialogue turn {index} speaker must be one of {list(_SPEAKERS)}"
            )
        text = str(turn.get("text", "")).strip()
        if not text:
            raise ValueError(f"podcast dialogue turn {index} text is empty")
        if len(text) > _MAX_TURN_CHARS:
            text = text[:_MAX_TURN_CHARS]
        cleaned.append({"speaker": speaker, "text": text})
    return {"title": title.strip(), "dialogue": cleaned}


async def generate_podcast_narration(
    *,
    lesson: Mapping[str, Any],
    language: str,
    run: StructuredRunner = run_structured_prompt,
) -> dict[str, Any]:
    """Generate a two-host dialogue script, with a source-only fallback.

    Returns a dict with ``title``, ``dialogue`` (list of ``{speaker, text}``),
    and ``script`` (a flattened display string for backward compatibility).
    On provider failure the result is ``degraded`` with a single-host fallback.
    """
    prompt = load_prompt(
        "courseware/podcast-script.md",
        {"language": language, "lesson": json.dumps(lesson, ensure_ascii=False)},
    )
    try:
        payload, metadata = await run(prompt, validate=_validate)
        dialogue = payload["dialogue"]
        return {
            "status": "completed",
            "title": str(payload["title"]).strip(),
            "dialogue": dialogue,
            "script": _dialogue_to_script(dialogue),
            "trace": asdict(metadata),
        }
    except Exception as exc:
        # Podcast is an optional presentation layer. A failed extra model call
        # must not discard the already-validated lesson or mutate learning state.
        fallback = fallback_podcast_script(lesson)
        if not fallback:
            raise
        fallback_dialogue = [{"speaker": "host", "text": fallback[:_MAX_TURN_CHARS]}]
        return {
            "status": "degraded",
            "title": _plain_text(lesson.get("title"))[:_MAX_TITLE_CHARS] or "Learning podcast",
            "dialogue": fallback_dialogue,
            "script": fallback,
            "message": f"{type(exc).__name__}: {exc}",
        }


__all__ = ["fallback_podcast_script", "generate_podcast_narration"]
