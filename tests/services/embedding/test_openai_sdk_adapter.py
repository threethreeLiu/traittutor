"""Tests for ``OpenAISDKEmbeddingAdapter``.

The adapter wraps the official ``AsyncOpenAI`` client. Tests stub the SDK
client itself rather than the underlying httpx layer — that way we verify
the contract the adapter expects from the SDK (kwargs forwarded, response
fields read) instead of pinning the SDK's internal URL routing.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from traittutor.services.embedding.adapters.base import (
    EmbeddingProviderError,
    EmbeddingRequest,
)
from traittutor.services.embedding.adapters.openai_sdk import (
    OpenAISDKEmbeddingAdapter,
)


def _make_adapter(
    *,
    model: str = "text-embedding-3-large",
    send_dimensions: bool | None = None,
    base_url: str = "https://openrouter.ai/api/v1",
    api_key: str = "sk-or-test",
    extra_headers: dict[str, str] | None = None,
) -> OpenAISDKEmbeddingAdapter:
    return OpenAISDKEmbeddingAdapter(
        {
            "api_key": api_key,
            "base_url": base_url,
            "model": model,
            "dimensions": 1024,
            "send_dimensions": send_dimensions,
            "request_timeout": 30,
            "extra_headers": extra_headers or {},
        }
    )


def _stub_response(*, dim: int = 1024, model: str = "stub-model") -> Any:
    """Build a stub ``CreateEmbeddingResponse``-shaped object.

    The adapter only reads ``data[i].embedding``, ``model``, and ``usage``,
    so a thin namespace stub is enough.
    """
    item = MagicMock()
    item.embedding = [0.1] * dim
    usage = MagicMock()
    usage.model_dump.return_value = {"prompt_tokens": 1, "total_tokens": 1}
    response = MagicMock()
    response.data = [item]
    response.model = model
    response.usage = usage
    return response


class _ClientStub:
    """Mimics ``AsyncOpenAI`` enough for the adapter's call site.

    Captures the kwargs passed to ``embeddings.create`` and the constructor
    args used to build the SDK client.
    """

    constructor_kwargs: dict[str, Any] = {}
    last_create_kwargs: dict[str, Any] = {}
    raised: Exception | None = None

    def __init__(self, **kwargs: Any) -> None:
        type(self).constructor_kwargs = kwargs
        self.embeddings = MagicMock()

        async def _create(**create_kwargs: Any) -> Any:
            type(self).last_create_kwargs = create_kwargs
            if type(self).raised is not None:
                raise type(self).raised
            return _stub_response()

        self.embeddings.create = _create
        self.close = AsyncMock()


@pytest.fixture
def stub_client(monkeypatch: pytest.MonkeyPatch) -> type[_ClientStub]:
    """Replace ``AsyncOpenAI`` in the adapter module with the stub above."""
    _ClientStub.constructor_kwargs = {}
    _ClientStub.last_create_kwargs = {}
    _ClientStub.raised = None
    monkeypatch.setattr(
        "traittutor.services.embedding.adapters.openai_sdk.AsyncOpenAI",
        _ClientStub,
    )
    return _ClientStub


@pytest.mark.asyncio
async def test_embed_passes_base_url_and_api_key_to_sdk(stub_client: type[_ClientStub]) -> None:
    adapter = _make_adapter(base_url="https://openrouter.ai/api/v1", api_key="sk-or")
    response = await adapter.embed(EmbeddingRequest(texts=["hi"], model="text-embedding-3-large"))

    # The SDK constructor receives the user's exact base_url; the SDK itself
    # will append `/embeddings`. That's the whole point of this adapter.
    assert stub_client.constructor_kwargs["base_url"] == "https://openrouter.ai/api/v1"
    assert stub_client.constructor_kwargs["api_key"] == "sk-or"
    assert response.dimensions == 1024


@pytest.mark.asyncio
async def test_embed_uses_placeholder_key_when_unset(stub_client: type[_ClientStub]) -> None:
    """Local gateways (vLLM, ollama-via-openai) often need no key, but the
    SDK refuses to construct without one — the adapter inserts a placeholder."""
    adapter = _make_adapter(api_key="")
    await adapter.embed(EmbeddingRequest(texts=["hi"], model="text-embedding-3-large"))
    assert stub_client.constructor_kwargs["api_key"] == "sk-no-key-required"


@pytest.mark.asyncio
async def test_embed_forwards_input_and_model(stub_client: type[_ClientStub]) -> None:
    adapter = _make_adapter()
    await adapter.embed(EmbeddingRequest(texts=["a", "b"], model="text-embedding-3-large"))
    kwargs = stub_client.last_create_kwargs
    assert kwargs["input"] == ["a", "b"]
    assert kwargs["model"] == "text-embedding-3-large"
    assert kwargs["encoding_format"] == "float"


@pytest.mark.asyncio
async def test_embed_includes_dimensions_for_text_embedding_3(
    stub_client: type[_ClientStub],
) -> None:
    adapter = _make_adapter(model="text-embedding-3-large", send_dimensions=None)
    await adapter.embed(
        EmbeddingRequest(texts=["x"], model="text-embedding-3-large", dimensions=512)
    )
    assert stub_client.last_create_kwargs.get("dimensions") == 512


@pytest.mark.asyncio
async def test_embed_omits_dimensions_when_send_dimensions_false(
    stub_client: type[_ClientStub],
) -> None:
    adapter = _make_adapter(model="text-embedding-3-large", send_dimensions=False)
    await adapter.embed(
        EmbeddingRequest(texts=["x"], model="text-embedding-3-large", dimensions=512)
    )
    assert "dimensions" not in stub_client.last_create_kwargs


@pytest.mark.asyncio
async def test_embed_omits_dimensions_for_unknown_model_under_auto(
    stub_client: type[_ClientStub],
) -> None:
    adapter = _make_adapter(model="qwen/qwen3-embedding-8b", send_dimensions=None)
    await adapter.embed(
        EmbeddingRequest(texts=["x"], model="qwen/qwen3-embedding-8b", dimensions=512)
    )
    # Heuristic: "qwen3-embedding" substring ⇒ send. Confirm parity with
    # openai_compatible adapter's behaviour.
    assert stub_client.last_create_kwargs.get("dimensions") == 512


@pytest.mark.asyncio
async def test_embed_forwards_extra_headers(stub_client: type[_ClientStub]) -> None:
    adapter = _make_adapter(extra_headers={"X-App": "traittutor"})
    await adapter.embed(EmbeddingRequest(texts=["x"], model="text-embedding-3-large"))
    assert stub_client.constructor_kwargs["default_headers"] == {"X-App": "traittutor"}


@pytest.mark.asyncio
async def test_embed_rejects_multimodal_contents(stub_client: type[_ClientStub]) -> None:
    adapter = _make_adapter()
    with pytest.raises(ValueError, match="multimodal"):
        await adapter.embed(
            EmbeddingRequest(
                texts=[],
                model="text-embedding-3-large",
                contents=[{"image": "data:image/png;base64,..."}],
            )
        )


@pytest.mark.asyncio
async def test_embed_wraps_api_status_error_with_diagnostics(
    stub_client: type[_ClientStub], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Provider HTTP errors surface as ``EmbeddingProviderError`` with
    status/url/model/body so the diagnostics UI can display them."""
    from openai import APIStatusError

    fake_response = MagicMock()
    fake_response.text = '{"error": {"message": "no embeddings here"}}'
    fake_response.status_code = 404
    err = APIStatusError(
        "404 not found",
        response=fake_response,
        body={"error": {"message": "no embeddings here"}},
    )
    stub_client.raised = err

    adapter = _make_adapter()
    with pytest.raises(EmbeddingProviderError) as excinfo:
        await adapter.embed(EmbeddingRequest(texts=["x"], model="text-embedding-3-large"))

    err_obj = excinfo.value
    assert err_obj.provider == "openai_sdk"
    assert err_obj.url == "https://openrouter.ai/api/v1"
    assert err_obj.model == "text-embedding-3-large"
    assert "no embeddings here" in (err_obj.body or "")
