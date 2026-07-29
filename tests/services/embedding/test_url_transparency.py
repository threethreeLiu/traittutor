"""Verify every embedding adapter posts to ``base_url`` VERBATIM.

This guards the v1.3.0 contract: what the user types in the Settings UI is
what hits the wire. No automatic ``/embeddings`` / ``/api/embed`` /
``/{api_version}/embed`` appending. Adapters get the URL once and use it.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from traittutor.services.embedding.adapters.base import EmbeddingRequest
from traittutor.services.embedding.adapters.cohere import CohereEmbeddingAdapter
from traittutor.services.embedding.adapters.jina import JinaEmbeddingAdapter
from traittutor.services.embedding.adapters.ollama import OllamaEmbeddingAdapter
from traittutor.services.embedding.adapters.openai_compatible import (
    OpenAICompatibleEmbeddingAdapter,
)

CUSTOM_URL = "https://internal-gateway.test/v999/foo/embeddings-bar"


def _capture_url(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Patch ``httpx.AsyncClient.post`` to record the URL it was called with."""
    captured: dict[str, Any] = {}

    real_init = httpx.AsyncClient.__init__

    def fake_init(self: httpx.AsyncClient, **kwargs: Any) -> None:  # noqa: ANN001
        real_init(self, **kwargs)

    async def fake_post(self: httpx.AsyncClient, url: str, **kwargs: Any) -> httpx.Response:
        captured["url"] = url
        captured["json"] = kwargs.get("json")
        captured["headers"] = kwargs.get("headers")
        request = httpx.Request("POST", url)
        # Different adapters expect different response shapes.
        return httpx.Response(
            status_code=200,
            json={
                "data": [{"embedding": [0.1, 0.2, 0.3]}],
                "embeddings": [[0.1, 0.2, 0.3]],
                "model": "test-model",
            },
            request=request,
        )

    monkeypatch.setattr(httpx.AsyncClient, "__init__", fake_init)
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    return captured


def test_public_embedding_providers_do_not_use_openai_sdk_autopath() -> None:
    from traittutor.services.config.provider_runtime import EMBEDDING_PROVIDERS

    for name, spec in EMBEDDING_PROVIDERS.items():
        if name == "custom_openai_sdk":
            continue
        assert spec.adapter != "openai_sdk", name


@pytest.mark.asyncio
async def test_openai_compat_url_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture_url(monkeypatch)
    adapter = OpenAICompatibleEmbeddingAdapter(
        {
            "api_key": "sk-test",
            "base_url": CUSTOM_URL,
            "model": "test-model",
            "dimensions": 0,
            "send_dimensions": False,
            "request_timeout": 5,
        }
    )
    await adapter.embed(EmbeddingRequest(texts=["hello"], model="test-model"))
    assert captured["url"] == CUSTOM_URL


@pytest.mark.asyncio
async def test_openai_compat_forwards_real_authorization_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _capture_url(monkeypatch)
    adapter = OpenAICompatibleEmbeddingAdapter(
        {
            "api_key": "sk-real",
            "base_url": CUSTOM_URL,
            "model": "test-model",
            "dimensions": 0,
            "send_dimensions": False,
            "request_timeout": 5,
        }
    )
    await adapter.embed(EmbeddingRequest(texts=["hello"], model="test-model"))
    assert captured["headers"]["Authorization"] == "Bearer sk-real"


@pytest.mark.asyncio
async def test_openai_compat_suppresses_no_key_placeholder_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _capture_url(monkeypatch)
    adapter = OpenAICompatibleEmbeddingAdapter(
        {
            "api_key": "sk-no-key-required",
            "base_url": CUSTOM_URL,
            "model": "test-model",
            "dimensions": 0,
            "send_dimensions": False,
            "request_timeout": 5,
        }
    )
    await adapter.embed(EmbeddingRequest(texts=["hello"], model="test-model"))
    assert "Authorization" not in captured["headers"]
    assert "api-key" not in captured["headers"]


@pytest.mark.asyncio
async def test_jina_url_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture_url(monkeypatch)

    # Jina parses `data["data"]` items; our fake response above includes that
    # shape so the adapter doesn't crash on parsing.
    adapter = JinaEmbeddingAdapter(
        {
            "api_key": "sk-test",
            "base_url": CUSTOM_URL,
            "model": "jina-embeddings-v3",
            "dimensions": 0,
            "send_dimensions": False,
            "request_timeout": 5,
        }
    )
    await adapter.embed(EmbeddingRequest(texts=["hello"], model="jina-embeddings-v3"))
    assert captured["url"] == CUSTOM_URL


@pytest.mark.asyncio
async def test_ollama_url_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture_url(monkeypatch)
    adapter = OllamaEmbeddingAdapter(
        {
            "api_key": "",
            "base_url": CUSTOM_URL,
            "model": "nomic-embed-text",
            "dimensions": 0,
            "request_timeout": 5,
        }
    )
    await adapter.embed(EmbeddingRequest(texts=["hello"], model="nomic-embed-text"))
    assert captured["url"] == CUSTOM_URL


@pytest.mark.asyncio
async def test_cohere_url_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture_url(monkeypatch)

    # Cohere v2 response: `embeddings.float` is the actual array of vectors.
    async def fake_post(self: httpx.AsyncClient, url: str, **kwargs: Any) -> httpx.Response:
        captured["url"] = url
        request = httpx.Request("POST", url)
        return httpx.Response(
            status_code=200,
            json={
                "embeddings": {"float": [[0.1, 0.2, 0.3]]},
                "model": "embed-v4.0",
            },
            request=request,
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    adapter = CohereEmbeddingAdapter(
        {
            "api_key": "co-test",
            "base_url": CUSTOM_URL,
            "model": "embed-v4.0",
            "api_version": "v2",
            "dimensions": 1024,
            "request_timeout": 5,
        }
    )
    await adapter.embed(EmbeddingRequest(texts=["hello"], model="embed-v4.0"))
    assert captured["url"] == CUSTOM_URL


@pytest.mark.asyncio
async def test_openai_compat_appends_only_azure_query_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`?api-version=...` is a query param, not a path component, so it
    is still appended even under the URL-transparency rule."""
    captured = _capture_url(monkeypatch)
    adapter = OpenAICompatibleEmbeddingAdapter(
        {
            "api_key": "az-test",
            "base_url": CUSTOM_URL,
            "model": "text-embedding-3-large",
            "api_version": "2024-02-01",
            "dimensions": 0,
            "send_dimensions": False,
            "request_timeout": 5,
        }
    )
    await adapter.embed(EmbeddingRequest(texts=["hi"], model="text-embedding-3-large"))
    assert captured["url"] == f"{CUSTOM_URL}?api-version=2024-02-01"
