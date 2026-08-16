from __future__ import annotations

import pytest

from traittutor.services.embedding.adapters.openai_compatible import (
    OpenAICompatibleEmbeddingAdapter,
)
from traittutor.services.embedding.adapters.openai_sdk import OpenAISDKEmbeddingAdapter


@pytest.mark.parametrize(
    "adapter_type",
    [OpenAICompatibleEmbeddingAdapter, OpenAISDKEmbeddingAdapter],
)
@pytest.mark.parametrize(
    ("send_dimensions", "model", "expected"),
    [
        (True, "unknown-model", True),
        (False, "text-embedding-3-small", False),
        (None, "text-embedding-3-large", True),
        (None, "Qwen3-Embedding-8B", True),
        (None, "Qwen3-VL-Embedding-2B", True),
        (None, "text-embedding-ada-002", False),
        (None, None, False),
    ],
)
def test_openai_adapters_share_dimensions_policy(
    adapter_type: type[OpenAICompatibleEmbeddingAdapter] | type[OpenAISDKEmbeddingAdapter],
    send_dimensions: bool | None,
    model: str | None,
    expected: bool,
) -> None:
    adapter = adapter_type(
        {
            "base_url": "https://example.test/v1/embeddings",
            "model": model or "",
            "send_dimensions": send_dimensions,
        }
    )

    assert adapter._should_send_openai_dimensions(model) is expected
