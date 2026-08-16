"""Video generation through the active model-catalog selection."""

from __future__ import annotations

from typing import Any

from traittutor.services.generation_http import GenerationProviderError
from traittutor.services.videogen.adapters import get_videogen_adapter
from traittutor.services.videogen.base import ProgressFn
from traittutor.services.videogen.config import VideogenConfig


async def generate_video(
    prompt: str,
    *,
    catalog: dict[str, Any] | None = None,
    aspect_ratio: str | None = None,
    duration: str | None = None,
    resolution: str | None = None,
    progress: ProgressFn | None = None,
) -> tuple[bytes, str]:
    """Generate one video without exposing provider task state to callers."""
    from traittutor.services.config.provider_runtime import resolve_videogen_runtime_config

    prompt = (prompt or "").strip()
    if not prompt:
        raise GenerationProviderError("Cannot generate a video from an empty prompt.")
    config = resolve_videogen_runtime_config(catalog=catalog)
    if aspect_ratio:
        config.aspect_ratio = aspect_ratio
    if duration:
        config.duration = duration
    if resolution:
        config.resolution = resolution
    return await get_videogen_adapter(config.adapter).generate(prompt, config, progress=progress)


async def probe_video(prompt: str, *, catalog: dict[str, Any] | None = None) -> str:
    """Submit a provider task without waiting for the completed video."""
    from traittutor.services.config.provider_runtime import resolve_videogen_runtime_config

    config = resolve_videogen_runtime_config(catalog=catalog)
    return await get_videogen_adapter(config.adapter).submit_task(
        (prompt or "").strip() or "A short educational test clip.", config
    )


__all__ = ["GenerationProviderError", "VideogenConfig", "generate_video", "probe_video"]
