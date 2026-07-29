"""Verify the ``_should_send_dimensions`` heuristic recognizes Qwen3-Embedding
and Qwen3-VL-Embedding model families (added in v1.3.0).
"""

from __future__ import annotations

import pytest

from traittutor.services.embedding.adapters.openai_compatible import (
    OpenAICompatibleEmbeddingAdapter,
)


def _make(model: str, send_dimensions: bool | None = None) -> OpenAICompatibleEmbeddingAdapter:
    return OpenAICompatibleEmbeddingAdapter(
        {
            "api_key": "sk-test",
            "base_url": "https://api.example.test/v1/embeddings",
            "model": model,
            "dimensions": 1024,
            "send_dimensions": send_dimensions,
            "request_timeout": 5,
        }
    )


@pytest.mark.parametrize(
    "model",
    [
        "Qwen/Qwen3-Embedding-8B",
        "Qwen/Qwen3-Embedding-4B",
        "Qwen/Qwen3-Embedding-0.6B",
        "qwen3-embedding-something",
    ],
)
def test_qwen3_embedding_family_auto_sends(model: str) -> None:
    adapter = _make(model)
    assert adapter._should_send_dimensions(model) is True


@pytest.mark.parametrize(
    "model",
    [
        "Qwen/Qwen3-VL-Embedding-8B",
        "qwen3-vl-embedding",
        "alias/qwen3-vl-embedding-test",
    ],
)
def test_qwen3_vl_embedding_family_auto_sends(model: str) -> None:
    adapter = _make(model)
    assert adapter._should_send_dimensions(model) is True


def test_explicit_false_overrides_heuristic_for_qwen3() -> None:
    adapter = _make("Qwen/Qwen3-VL-Embedding-8B", send_dimensions=False)
    assert adapter._should_send_dimensions("Qwen/Qwen3-VL-Embedding-8B") is False


def test_unrelated_model_still_skipped() -> None:
    adapter = _make("text-embedding-ada-002")
    assert adapter._should_send_dimensions("text-embedding-ada-002") is False
