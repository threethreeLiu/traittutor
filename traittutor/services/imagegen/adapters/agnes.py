"""Agnes image-generation adapter.

Agnes exposes the OpenAI-style Images endpoint but requires ``response_format``
inside ``extra_body`` and accepts a ``ratio`` control.  Keep this difference
here rather than weakening the generic OpenAI-compatible adapter.
"""

from __future__ import annotations

from typing import Any

import httpx

from traittutor.services.generation_http import (
    GenerationProviderError,
    build_auth_headers,
    join_api_path,
    raise_for_provider,
)
from traittutor.services.imagegen.adapters.openai_compat import OpenAICompatImagegenAdapter
from traittutor.services.imagegen.base import BaseImagegenAdapter
from traittutor.services.imagegen.config import ImagegenConfig


class AgnesImagegenAdapter(BaseImagegenAdapter):
    """Generate a single Agnes image and materialize its URL/base64 response."""

    async def generate(
        self, prompt: str, config: ImagegenConfig, *, n: int = 1
    ) -> list[tuple[bytes, str]]:
        if not config.base_url:
            raise GenerationProviderError("No endpoint URL configured for Agnes image generation.")
        url = join_api_path(config.base_url, "images/generations")
        payload: dict[str, Any] = {
            "model": config.model,
            "prompt": prompt,
            "size": config.size or "1K",
            "extra_body": {"response_format": config.response_format or "url"},
        }
        if config.ratio:
            payload["ratio"] = config.ratio
        headers = {
            "Content-Type": "application/json",
            **build_auth_headers(config.auth_style, config.api_key),
            **(config.extra_headers or {}),
        }
        try:
            async with httpx.AsyncClient(timeout=config.request_timeout) as client:
                response = await client.post(url, headers=headers, json=payload)
                raise_for_provider(response, "Agnes image generation")
                return [
                    await OpenAICompatImagegenAdapter()._materialize(client, item)
                    for item in OpenAICompatImagegenAdapter._extract_items(response)
                ]
        except httpx.HTTPError as exc:
            raise GenerationProviderError(f"Agnes image generation request error: {exc}") from exc


__all__ = ["AgnesImagegenAdapter"]
