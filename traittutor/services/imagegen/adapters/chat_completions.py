"""Chat-completions image-generation adapter, routed through the Gateway.

Some gateways generate images through the chat endpoint rather than the OpenAI
Images API: ``POST {base}/chat/completions`` with ``modalities: ["image",
"text"]`` returns the image inside the assistant message::

    {"choices": [{"message": {"images": [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}
    ]}}]}

This covers OpenRouter image models (Flux, Gemini image, …). The model call
itself goes through :class:`TraitTutorGateway` as a typed image-modality
completion (single audited boundary, receipt + usage accounting), so this
adapter owns no HTTP transport for generation. Materializing the returned
media — decoding base64 data URIs, downloading provider-hosted image URLs —
is a storage fetch, not a model call, and stays local httpx.

Quota rotation and route fallback are explicitly off: fallback routes are
enumerated from the text-LLM catalog, and rotating an image-modality request
onto a text model would silently produce a no-image response instead of a
recoverable failure.
"""

from __future__ import annotations

import base64
import logging

import httpx

from traittutor.services.generation_http import (
    GenerationProviderError,
    build_auth_headers,
    raise_for_provider,
)
from traittutor.services.imagegen.base import BaseImagegenAdapter
from traittutor.services.imagegen.config import ImagegenConfig

logger = logging.getLogger(__name__)


class ChatCompletionsImagegenAdapter(BaseImagegenAdapter):
    """Typed image-modality chat completion through the Gateway."""

    async def generate(
        self, prompt: str, config: ImagegenConfig, *, n: int = 1
    ) -> list[tuple[bytes, str]]:
        if not config.base_url:
            raise GenerationProviderError("No endpoint URL configured for image generation.")
        del n  # chat-modality models decide image count themselves; one call, all parts

        # Imported lazily: pulling the gateway/LLM config at module level
        # creates a circular import through services.llm.config.
        from traittutor.gateway.service import GatewayRequest, TraitTutorGateway
        from traittutor.services.llm.config import LLMConfig

        llm_config = LLMConfig(
            model=config.model,
            api_key=config.api_key if config.auth_style == "bearer" else "",
            base_url=config.base_url,
            provider_name=config.provider_name or "imagegen",
            extra_headers=(
                {
                    **(
                        build_auth_headers(config.auth_style, config.api_key)
                        if config.auth_style != "bearer"
                        else {}
                    ),
                    **(config.extra_headers or {}),
                }
                or None
            ),
        )
        request = GatewayRequest(
            prompt=prompt,
            system_prompt="",
            purpose="imagegen",
            llm_config=llm_config,
            timeout_seconds=float(config.request_timeout),
            image_modalities=True,
            allow_quota_rotation=False,
            allow_route_fallback=False,
        )

        logger.debug("imagegen(chat/gateway) base=%s model=%s", config.base_url, config.model)
        response = await TraitTutorGateway().complete(request)
        sources = [
            url for part in response.images if isinstance((url := part.get("url")), str) and url
        ]
        if not sources:
            raise GenerationProviderError(
                "Chat model returned no image. Check the model supports image output "
                "(its output modalities must include `image`)."
            )
        return [await self._materialize(src) for src in sources]

    async def _materialize(self, src: str) -> tuple[bytes, str]:
        """Decode a data URI or download a provider-hosted image URL.

        This is media retrieval, not a model call, so it stays on local httpx
        outside the Gateway boundary.
        """
        if src.startswith("data:"):
            header, _, encoded = src.partition(",")
            if not encoded:
                raise GenerationProviderError("Malformed image data URI.")
            content_type = header[5:].split(";", 1)[0].strip() or "image/png"
            return base64.b64decode(encoded), content_type
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.get(src)
            raise_for_provider(resp, "Image download")
            content_type = resp.headers.get("content-type") or "image/png"
            if not content_type.startswith("image/"):
                content_type = "image/png"
            return resp.content, content_type


__all__ = ["ChatCompletionsImagegenAdapter"]
