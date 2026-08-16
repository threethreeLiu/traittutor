"""Embedding adapter implementations and backend registry."""

from .base import (
    BaseEmbeddingAdapter,
    EmbeddingProviderError,
    EmbeddingRequest,
    EmbeddingResponse,
)
from .cohere import CohereEmbeddingAdapter
from .dashscope_native import DashScopeMultiModalEmbeddingAdapter
from .jina import JinaEmbeddingAdapter
from .ollama import OllamaEmbeddingAdapter
from .openai_compatible import OpenAICompatibleEmbeddingAdapter
from .openai_sdk import OpenAISDKEmbeddingAdapter

ADAPTER_BACKENDS: dict[str, type[BaseEmbeddingAdapter]] = {
    "openai_compat": OpenAICompatibleEmbeddingAdapter,
    "openai_sdk": OpenAISDKEmbeddingAdapter,
    "cohere": CohereEmbeddingAdapter,
    "jina": JinaEmbeddingAdapter,
    "ollama": OllamaEmbeddingAdapter,
    "dashscope_native": DashScopeMultiModalEmbeddingAdapter,
}

__all__ = [
    "ADAPTER_BACKENDS",
    "BaseEmbeddingAdapter",
    "EmbeddingProviderError",
    "EmbeddingRequest",
    "EmbeddingResponse",
    "OpenAICompatibleEmbeddingAdapter",
    "OpenAISDKEmbeddingAdapter",
    "DashScopeMultiModalEmbeddingAdapter",
    "JinaEmbeddingAdapter",
    "CohereEmbeddingAdapter",
    "OllamaEmbeddingAdapter",
]
