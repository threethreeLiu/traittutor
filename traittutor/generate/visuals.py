"""Source-bounded visual assets for TraitTutor learning artifacts.

The text artifact is always validated first.  A missing or failed image provider
therefore never turns a valid lesson, card set, or quiz into a failed learning
task.  Images are saved in the authenticated public-output workspace rather
than retaining an expiring provider URL.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import re
from typing import Any, Mapping
from urllib.parse import quote

from traittutor.services.generation_http import GenerationProviderError
from traittutor.services.imagegen import generate_image
from traittutor.services.path_service import get_path_service

_EXTENSIONS = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}


def _clean(value: object, *, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _prompt(result: Mapping[str, Any]) -> str:
    title = _clean(result.get("title"), limit=120)
    if result.get("kind") == "courseware":
        sections = result.get("sections") or []
        first = sections[0] if isinstance(sections, list) and sections else {}
        focus = _clean((first or {}).get("core_content"), limit=420)
    else:
        items = result.get("items") or []
        first = items[0] if isinstance(items, list) and items else {}
        focus = _clean((first or {}).get("back") or (first or {}).get("question"), limit=420)
    return (
        "Create one clear, accurate educational illustration for the learning material. "
        "Depict only the supplied concept, with no personality labels, diagnostic claims, "
        "scores, answer keys, decorative text, or invented facts. "
        f"Topic: {title}. Source-grounded focus: {focus}."
    )


def merge_learning_visual(result: dict[str, Any], visual: dict[str, Any]) -> None:
    result.setdefault("images", []).append(visual)
    if result.get("kind") == "courseware":
        sections = result.get("sections")
        if isinstance(sections, list) and sections and isinstance(sections[0], dict):
            sections[0].setdefault("images", []).append(visual)
    else:
        items = result.get("items")
        if isinstance(items, list) and items and isinstance(items[0], dict):
            items[0].setdefault("images", []).append(visual)


async def generate_learning_visual(
    prompt_source: Mapping[str, Any],
    *,
    generation_id: str,
) -> dict[str, Any]:
    """Generate a visual from a source-bounded seed without mutating an artifact."""
    prompt = _prompt(prompt_source)
    ratio = "16:9" if prompt_source.get("kind") == "courseware" else "1:1"
    size = "2K" if prompt_source.get("kind") == "courseware" else "1K"
    trace: dict[str, Any] = {
        "status": "unavailable",
        "prompt": prompt,
        "placement": "section" if prompt_source.get("kind") == "courseware" else prompt_source.get("kind"),
        "created_at": datetime.now(UTC).isoformat(),
    }
    try:
        images = await generate_image(prompt, size=size, ratio=ratio, n=1)
        data, content_type = images[0]
        service = get_path_service()
        media_dir = service.get_task_workspace("chat", f"traittutor-{generation_id}") / "media"
        media_dir.mkdir(parents=True, exist_ok=True)
        suffix = _EXTENSIONS.get(content_type.split(";", 1)[0].strip(), "png")
        path = media_dir / f"learning-visual.{suffix}"
        path.write_bytes(data)
        relative = path.resolve().relative_to(service.get_public_outputs_root().resolve()).as_posix()
        visual = {
            "url": "/api/outputs/" + quote(relative, safe="/"),
            "alt": _clean(prompt_source.get("title"), limit=180) or "Learning illustration",
            "placement": trace["placement"],
            "provider": "configured_imagegen",
            "content_type": content_type,
        }
        trace.update({"status": "completed", "asset": visual})
    except (ValueError, GenerationProviderError, OSError, IndexError) as exc:
        # Text remains useful; surface a configuration/provider status to the UI.
        trace.update({"status": "failed", "message": str(exc)})
    return trace


async def attach_learning_visual(
    result: dict[str, Any],
    *,
    generation_id: str,
) -> dict[str, Any]:
    """Compatibility helper for callers that do not start image work early."""
    trace = await generate_learning_visual(result, generation_id=generation_id)
    asset = trace.get("asset")
    if isinstance(asset, dict):
        merge_learning_visual(result, asset)
    result["image_generation"] = trace
    return trace


__all__ = ["attach_learning_visual", "generate_learning_visual", "merge_learning_visual"]
