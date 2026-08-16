"""Provider configuration and capability helpers below the typed Gateway.

Product and Agent code must call :mod:`traittutor.gateway`; this package keeps
provider implementations and compatibility adapters only.
"""

# Note: cloud_provider and local_provider are lazy-loaded via __getattr__
# to avoid importing optional heavy dependencies at module load time
from .capabilities import (
    DEFAULT_CAPABILITIES,
    MODEL_OVERRIDES,
    PROVIDER_CAPABILITIES,
    get_capability,
    has_thinking_tags,
    requires_api_version,
    supports_response_format,
    supports_streaming,
    supports_tools,
    supports_vision,
    system_in_messages,
)
from .client import LLMClient, get_llm_client, reset_llm_client
from .config import (
    LLMConfig,
    clear_llm_config_cache,
    get_llm_config,
    get_token_limit_kwargs,
    reload_config,
    uses_max_completion_tokens,
)
from .exceptions import (
    LLMAPIError,
    LLMAuthenticationError,
    LLMConfigError,
    LLMError,
    LLMModelNotFoundError,
    LLMProviderError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from .model_discovery import fetch_models
from .multimodal import MultimodalResult, prepare_multimodal_messages
from .utils import (
    build_auth_headers,
    build_chat_url,
    clean_thinking_tags,
    extract_response_content,
    is_local_llm_server,
    sanitize_url,
)

__all__ = [
    # Client compatibility adapter (public calls enter Gateway)
    "LLMClient",
    "get_llm_client",
    "reset_llm_client",
    # Config
    "LLMConfig",
    "get_llm_config",
    "clear_llm_config_cache",
    "reload_config",
    "uses_max_completion_tokens",
    "get_token_limit_kwargs",
    # Capabilities
    "PROVIDER_CAPABILITIES",
    "MODEL_OVERRIDES",
    "DEFAULT_CAPABILITIES",
    "get_capability",
    "supports_response_format",
    "supports_streaming",
    "system_in_messages",
    "has_thinking_tags",
    "supports_tools",
    "supports_vision",
    "requires_api_version",
    # Multimodal
    "MultimodalResult",
    "prepare_multimodal_messages",
    # Exceptions
    "LLMError",
    "LLMConfigError",
    "LLMProviderError",
    "LLMAPIError",
    "LLMTimeoutError",
    "LLMRateLimitError",
    "LLMAuthenticationError",
    "LLMModelNotFoundError",
    # Provider model discovery (no inference)
    "fetch_models",
    # Providers (lazy loaded)
    "cloud_provider",
    "local_provider",
    # Utils
    "sanitize_url",
    "is_local_llm_server",
    "build_chat_url",
    "build_auth_headers",
    "clean_thinking_tags",
    "extract_response_content",
]


def __getattr__(name: str):
    """Lazy import for provider modules that depend on heavy libraries."""
    from importlib import import_module

    if name == "cloud_provider":
        return import_module("traittutor.services.llm.cloud_provider")
    if name == "local_provider":
        return import_module("traittutor.services.llm.local_provider")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
