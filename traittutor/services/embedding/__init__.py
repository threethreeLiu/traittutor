"""Unified embedding client and adapters for all TraitTutor modules.

Supported bindings are resolved by ``services.config.provider_runtime`` and
currently include openai, custom, azure_openai, cohere, jina, ollama, vllm,
siliconflow, aliyun, openrouter, plus legacy custom_openai_sdk configs.
"""

from .adapters import (
    BaseEmbeddingAdapter,
    CohereEmbeddingAdapter,
    DashScopeMultiModalEmbeddingAdapter,
    EmbeddingProviderError,
    EmbeddingRequest,
    EmbeddingResponse,
    JinaEmbeddingAdapter,
    OllamaEmbeddingAdapter,
    OpenAICompatibleEmbeddingAdapter,
    OpenAISDKEmbeddingAdapter,
)
from .client import EmbeddingClient, get_embedding_client, reset_embedding_client
from .config import EmbeddingConfig, get_embedding_config
from .validation import validate_embedding_batch

__all__ = [
    "EmbeddingClient",
    "EmbeddingConfig",
    "get_embedding_client",
    "get_embedding_config",
    "reset_embedding_client",
    "validate_embedding_batch",
    "BaseEmbeddingAdapter",
    "EmbeddingProviderError",
    "EmbeddingRequest",
    "EmbeddingResponse",
    "OpenAICompatibleEmbeddingAdapter",
    "OpenAISDKEmbeddingAdapter",
    "DashScopeMultiModalEmbeddingAdapter",
    "CohereEmbeddingAdapter",
    "JinaEmbeddingAdapter",
    "OllamaEmbeddingAdapter",
]
