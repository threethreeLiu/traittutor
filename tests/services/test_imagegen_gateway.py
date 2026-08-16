"""Typed image-modality gateway integration for the chat-completions adapter.

The adapter must not own an HTTP transport for the model call: it builds a
``GatewayRequest(image_modalities=True)`` and materializes whatever image
parts the Gateway response carries.
"""

from __future__ import annotations

import base64

import pytest

from traittutor.services.generation_http import GenerationProviderError
from traittutor.services.imagegen.adapters.chat_completions import (
    ChatCompletionsImagegenAdapter,
)
from traittutor.services.imagegen.config import ImagegenConfig


def _config() -> ImagegenConfig:
    return ImagegenConfig(
        model="flux-image",
        provider_name="imagegen",
        adapter="chat_completions",
        api_key="key-imagegen",
        base_url="https://image.example/v1",
    )


class _Response:
    def __init__(self, images: list[dict]) -> None:
        self.images = images


class _Gateway:
    """Records the request and answers with a canned image-part list."""

    last_request = None

    async def complete(self, request) -> _Response:  # noqa: ANN001
        _Gateway.last_request = request
        return _Response(getattr(self, "images", []))


@pytest.mark.asyncio
async def test_generate_routes_through_gateway_with_image_modalities(monkeypatch) -> None:
    png = base64.b64encode(b"png-bytes").decode()
    gateway = _Gateway()
    gateway.images = [{"url": f"data:image/png;base64,{png}"}]
    monkeypatch.setattr("traittutor.gateway.service.TraitTutorGateway", lambda: gateway)

    media = await ChatCompletionsImagegenAdapter().generate("a calm study desk", _config())

    request = _Gateway.last_request
    assert request is not None
    assert request.purpose == "imagegen"
    assert request.image_modalities is True
    # Fallback/rotation to text-LLM routes must stay off for image requests.
    assert request.allow_route_fallback is False
    assert request.allow_quota_rotation is False
    assert request.llm_config.model == "flux-image"
    assert request.llm_config.api_key == "key-imagegen"

    assert media == [(b"png-bytes", "image/png")]


@pytest.mark.asyncio
async def test_generate_without_image_parts_fails_with_actionable_error(
    monkeypatch,
) -> None:
    gateway = _Gateway()
    gateway.images = []
    monkeypatch.setattr("traittutor.gateway.service.TraitTutorGateway", lambda: gateway)

    with pytest.raises(GenerationProviderError, match="no image"):
        await ChatCompletionsImagegenAdapter().generate("no image model", _config())


@pytest.mark.asyncio
async def test_generate_requires_configured_base_url() -> None:
    config = _config()
    config.base_url = ""
    with pytest.raises(GenerationProviderError, match="No endpoint URL"):
        await ChatCompletionsImagegenAdapter().generate("prompt", config)
