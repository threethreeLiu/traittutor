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
_VISUAL_SUPPORT_DIMENSIONS = {"goal_planning", "monitoring_regulation", "reflection_transfer"}
_VISUAL_ACTION_KEYWORDS = (
    "可见",
    "清单",
    "小步",
    "自检",
    "总结",
    "新例子",
    "progress",
    "checklist",
    "step",
    "self-check",
    "summary",
    "example",
    "visual",
    "diagram",
    "structure",
)


def _clean(value: object, *, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _float_or_zero(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _prompt(result: Mapping[str, Any]) -> str:
    title = _clean(result.get("title"), limit=120)
    visual_targets = result.get("visual_targets") or []
    target_labels = ", ".join(
        _clean((target or {}).get("label") or (target or {}).get("concept_id"), limit=80)
        for target in visual_targets
        if isinstance(target, Mapping)
    )
    support_reason = _clean(result.get("slr_visual_reason"), limit=240)
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
        f"Topic: {title}. Source-grounded focus: {focus}. "
        f"SLR support reason: {support_reason or 'visual structure support'}. "
        f"Visual targets: {target_labels or 'the focal source concept'}."
    )


def merge_learning_visual(result: dict[str, Any], visual: dict[str, Any]) -> None:
    result.setdefault("images", []).append(visual)
    component_id = str(visual.get("component_id") or "")
    if component_id:
        result.setdefault("component_media", {}).setdefault(component_id, []).append(visual)
        # Component-mode generation is rendered inside its own canvas slot.
        # Do not also pin the image onto an unrelated first section/item.
        return
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
    max_attempts: int = 2,
) -> dict[str, Any]:
    """Generate a visual from a source-bounded seed without mutating an artifact."""
    prompt = _prompt(prompt_source)
    ratio = "16:9" if prompt_source.get("kind") == "courseware" else "1:1"
    size = "2K" if prompt_source.get("kind") == "courseware" else "1K"
    attempts = max(1, int(max_attempts or 1))
    trace: dict[str, Any] = {
        "status": "unavailable",
        "prompt": prompt,
        "placement": "section" if prompt_source.get("kind") == "courseware" else prompt_source.get("kind"),
        "created_at": datetime.now(UTC).isoformat(),
        "attempts": [],
    }
    for attempt in range(1, attempts + 1):
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
                "component_id": _clean(prompt_source.get("component_id"), limit=160) or None,
            }
            trace["attempts"].append({"attempt": attempt, "status": "completed"})
            trace.update({"status": "completed", "asset": visual})
            break
        except (ValueError, GenerationProviderError, OSError, IndexError) as exc:
            # Text remains useful; surface a configuration/provider status to the UI.
            trace["attempts"].append({"attempt": attempt, "status": "failed", "message": str(exc)})
            trace.update({"status": "failed", "message": str(exc)})
    return trace


def should_generate_learning_visual(
    *,
    slr_support: Mapping[str, Any] | None,
    learning_targets: Mapping[str, Any] | None,
    generation_type: str,
) -> dict[str, Any]:
    """Decide whether SLR-supported generation actually needs a visual.

    Images are an aid, not a default decoration.  The gate requires both a
    source-derived visual target and an SLR support signal that benefits from a
    visual or structural representation.
    """
    targets = [
        dict(target)
        for target in list((learning_targets or {}).get("visual_targets") or [])
        if isinstance(target, Mapping)
    ][:2]
    if not targets:
        return {
            "should_generate": False,
            "reason": "no_visual_targets",
            "generation_type": generation_type,
            "visual_targets": [],
            "support_reasons": [],
        }

    support = dict(slr_support or {})
    dimensions = dict(support.get("dimensions") or {})
    support_reasons: list[str] = []
    for key, value in dimensions.items():
        if not isinstance(value, Mapping):
            continue
        emphasis = str(value.get("emphasis") or "")
        actions = [str(item) for item in list(value.get("actions") or [])]
        strong_visual_dimension = key in _VISUAL_SUPPORT_DIMENSIONS and emphasis == "strong"
        action_mentions_visual_structure = any(
            keyword.lower() in action.lower()
            for action in actions
            for keyword in _VISUAL_ACTION_KEYWORDS
        )
        if strong_visual_dimension or (emphasis == "strong" and action_mentions_visual_structure):
            support_reasons.append(str(value.get("label") or key))

    profile = dict(support.get("generation_support_profile") or {})
    needs = dict(profile.get("learner_support_profile") or {})
    high_needs = [
        key
        for key in ("structure_need", "scaffolding_need", "conceptual_depth_readiness")
        if _float_or_zero(needs.get(key)) >= 4
    ]
    support_reasons.extend(high_needs)
    deduped_reasons = list(dict.fromkeys(support_reasons))
    return {
        "should_generate": bool(deduped_reasons),
        "reason": "slr_visual_support" if deduped_reasons else "no_slr_visual_support",
        "generation_type": generation_type,
        "visual_targets": targets,
        "support_reasons": deduped_reasons,
    }


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


__all__ = [
    "attach_learning_visual",
    "generate_learning_visual",
    "merge_learning_visual",
    "should_generate_learning_visual",
]
