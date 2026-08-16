"""Source-bounded optional videos for learning components."""

from __future__ import annotations

from datetime import UTC, datetime
import re
from typing import Any, Mapping
from urllib.parse import quote

from traittutor.services.generation_http import GenerationProviderError
from traittutor.services.path_service import get_path_service
from traittutor.services.videogen import generate_video

_EXTENSIONS = {"video/mp4": "mp4", "video/webm": "webm", "video/quicktime": "mov"}


def _clean(value: object, *, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _prompt(source: Mapping[str, Any]) -> str:
    title = _clean(source.get("title"), limit=120)
    sections = source.get("sections") or []
    first = sections[0] if isinstance(sections, list) and sections else {}
    focus = _clean((first or {}).get("core_content"), limit=500)
    if not focus:
        items = source.get("items") or []
        first_item = items[0] if isinstance(items, list) and items else {}
        focus = _clean((first_item or {}).get("back"), limit=500)
    targets = ", ".join(
        _clean((target or {}).get("label") or (target or {}).get("concept_id"), limit=80)
        for target in source.get("visual_targets") or []
        if isinstance(target, Mapping)
    )
    return (
        "Create a short, calm educational animation grounded only in the supplied course material. "
        "Show the concept changing or unfolding over time. Do not add narration, scores, answer keys, "
        "diagnostic claims, personality labels, decorative text, logos, or invented facts. "
        f"Topic: {title}. Source-grounded focus: {focus}. "
        f"Concept targets: {targets or title}."
    )


def merge_learning_video(result: dict[str, Any], video: dict[str, Any]) -> None:
    result.setdefault("videos", []).append(video)
    component_id = str(video.get("component_id") or "")
    if component_id:
        result.setdefault("component_media", {}).setdefault(component_id, []).append(video)


async def generate_learning_video(
    prompt_source: Mapping[str, Any],
    *,
    generation_id: str,
) -> dict[str, Any]:
    """Generate and persist one optional video without mutating learning state."""
    prompt = _prompt(prompt_source)
    trace: dict[str, Any] = {
        "status": "unavailable",
        "prompt": prompt,
        "created_at": datetime.now(UTC).isoformat(),
    }
    try:
        data, content_type = await generate_video(
            prompt,
            aspect_ratio="16:9",
            duration="5",
            resolution="720p",
        )
        service = get_path_service()
        media_dir = service.get_task_workspace("chat", f"traittutor-{generation_id}") / "media"
        media_dir.mkdir(parents=True, exist_ok=True)
        suffix = _EXTENSIONS.get(content_type.split(";", 1)[0].strip(), "mp4")
        path = media_dir / f"learning-video.{suffix}"
        path.write_bytes(data)
        relative = (
            path.resolve().relative_to(service.get_public_outputs_root().resolve()).as_posix()
        )
        asset = {
            "url": "/api/outputs/" + quote(relative, safe="/"),
            "alt": _clean(prompt_source.get("title"), limit=180)
            or "Source-grounded learning animation",
            "provider": "configured_videogen",
            "content_type": content_type,
            "component_id": _clean(prompt_source.get("component_id"), limit=160) or None,
        }
        trace.update({"status": "completed", "asset": asset})
    except (ValueError, GenerationProviderError, OSError) as exc:
        # Video is an optional support. Its provider may be slow, unavailable,
        # or quota-limited without affecting the validated text lesson.
        trace.update({"status": "failed", "message": str(exc)})
    return trace


__all__ = ["generate_learning_video", "merge_learning_video"]
