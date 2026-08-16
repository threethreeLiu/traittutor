"""Compatibility client whose public calls are routed through the Gateway."""

from collections.abc import Callable, Mapping
import logging
from typing import Any, cast

from .capabilities import supports_vision
from .config import LLMConfig, get_llm_config
from .error_mapping import map_error
from .provider_factory import get_runtime_provider


class LLMClient:
    """Narrow compatibility adapter for integrations that need a client object.

    Public completion methods enter the typed Gateway. The private
    ``complete_with_usage`` transport is called only by the Gateway after it
    has applied capability gating, retry/fallback policy, and deadlines.
    """

    def __init__(self, config: LLMConfig | None = None) -> None:
        """
        Initialize LLM client.

        Args:
            config: LLM configuration. If None, loads from environment.
        """

        self.config = config or get_llm_config()
        self.logger = logging.getLogger(__name__)

        # Deliberately NO OPENAI_* process-env sync here. This client is
        # constructed per request by the gateway; writing the plaintext key
        # into os.environ would leak the last caller's secret to every
        # subprocess and any library that reads the environment (and one
        # user's key would overwrite another's in multi-user deployments).
        # Provider SDK clients receive credentials explicitly; the one-time
        # module-import sync for env-reading SDK helpers lives in
        # ``services/llm/config.py``.

    async def complete(
        self,
        prompt: str,
        system_prompt: str | None = None,
        history: list[dict[str, str]] | None = None,
        **kwargs: object,
    ) -> str:
        """Return text through the typed Gateway compatibility boundary."""
        from traittutor.gateway import (
            GatewayAttachment,
            GatewayMessage,
            GatewayRequest,
            GatewayTool,
            get_gateway,
        )

        options: dict[str, Any] = dict(kwargs)
        raw_max_tokens = options.pop("max_tokens", None)
        if raw_max_tokens is None:
            raw_max_tokens = options.pop("max_completion_tokens", None)
        raw_temperature = options.pop("temperature", None)
        raw_timeout = options.pop("timeout_seconds", None)
        raw_retries = options.pop("max_retries", None)
        response_format = options.pop("response_format", None)
        reasoning_effort = options.pop("reasoning_effort", None)
        image_data = options.pop("image_data", None)
        image_mime_type = str(options.pop("image_mime_type", "image/png") or "image/png")
        image_filename = str(options.pop("image_filename", "image.png") or "image.png")
        raw_tools = options.pop("tools", ())
        # LightRAG supplies cache bookkeeping to its model hook. It is not a
        # provider argument and must not cross the Gateway boundary.
        for integration_only in ("hashing_kv", "keyword_extraction", "cache_prompt"):
            options.pop(integration_only, None)
        if options:
            raise TypeError("Unsupported LLMClient Gateway options: " + ", ".join(sorted(options)))

        default_system = system_prompt or "You are a helpful assistant."
        messages = tuple(
            GatewayMessage.from_mapping(cast(Mapping[str, Any], item)) for item in (history or [])
        ) or (
            GatewayMessage(role="system", content=default_system),
            GatewayMessage(role="user", content=prompt),
        )
        attachments: tuple[GatewayAttachment, ...] = ()
        if isinstance(image_data, str) and image_data:
            attachments = (
                GatewayAttachment(
                    type="image",
                    filename=image_filename,
                    mime_type=image_mime_type,
                    base64=image_data,
                ),
            )
        if raw_tools is None:
            tools: tuple[GatewayTool, ...] = ()
        elif isinstance(raw_tools, (list, tuple)):
            tools = tuple(
                GatewayTool.from_mapping(cast(Mapping[str, Any], item)) for item in raw_tools
            )
        else:
            raise TypeError("LLMClient tools must be a list or tuple")
        config = self.config
        if isinstance(reasoning_effort, str):
            config = config.model_copy({"reasoning_effort": reasoning_effort})
        response = await get_gateway().complete(
            GatewayRequest(
                prompt=prompt,
                system_prompt=default_system,
                purpose="compat:llm-client",
                messages=messages,
                attachments=attachments,
                tools=tools,
                response_format=(
                    cast(Mapping[str, Any], response_format)
                    if isinstance(response_format, Mapping)
                    else None
                ),
                temperature=(
                    float(raw_temperature)
                    if isinstance(raw_temperature, (int, float))
                    and not isinstance(raw_temperature, bool)
                    else None
                ),
                max_tokens=(
                    int(raw_max_tokens)
                    if isinstance(raw_max_tokens, (int, float))
                    and not isinstance(raw_max_tokens, bool)
                    else None
                ),
                max_retries=(
                    int(raw_retries)
                    if isinstance(raw_retries, int) and not isinstance(raw_retries, bool)
                    else None
                ),
                timeout_seconds=(
                    float(raw_timeout)
                    if isinstance(raw_timeout, (int, float)) and not isinstance(raw_timeout, bool)
                    else None
                ),
                llm_config=config,
            )
        )
        return response.content

    async def complete_with_usage(
        self,
        prompt: str,
        system_prompt: str | None = None,
        history: list[dict[str, str]] | None = None,
        **kwargs: object,
    ) -> tuple[str, dict[str, int], list[dict[str, Any]]]:
        """Gateway-owned provider transport with inner retries disabled.

        Returns ``(content, usage, images)``; ``images`` is non-empty only
        for image-modality requests routed through the Gateway."""
        del prompt, system_prompt
        options: dict[str, Any] = dict(kwargs)
        options.pop("_skip_quota_rotation", None)
        options.pop("max_retries", None)
        tools = options.pop("tools", None)
        provider = get_runtime_provider(self.config)
        try:
            response = await provider.chat_with_retry(
                messages=cast(list[dict[str, Any]], history or []),
                tools=cast(list[dict[str, Any]] | None, tools),
                model=self.config.model,
                reasoning_effort=self.config.reasoning_effort,
                retry_delays=(),
                allow_image_fallback=not supports_vision(self.config.binding, self.config.model),
                **options,
            )
        except Exception as exc:
            raise map_error(exc, provider=self.config.provider_name) from exc
        if response.finish_reason == "error":
            raise map_error(
                RuntimeError(response.content or "LLM request failed"),
                provider=self.config.provider_name,
            )
        raw_usage = getattr(response, "usage", {})
        usage = {
            str(key): value
            for key, value in raw_usage.items()
            if (
                isinstance(key, str)
                and isinstance(value, int)
                and not isinstance(value, bool)
                and value >= 0
            )
        }
        return (
            str(getattr(response, "content", "") or ""),
            usage,
            list(getattr(response, "images", None) or []),
        )

    def complete_sync(
        self,
        prompt: str,
        system_prompt: str | None = None,
        history: list[dict[str, str]] | None = None,
        **kwargs: object,
    ) -> str:
        """
        Synchronous wrapper for complete().

        Use this when you need to call from non-async context.
        """
        import asyncio

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # No running event loop -> safe to run synchronously.
            return asyncio.run(self.complete(prompt, system_prompt, history, **kwargs))

        raise RuntimeError(
            "LLMClient.complete_sync() cannot be called from a running event loop. "
            "Use `await llm.complete(...)` instead."
        )

    def get_model_func(self) -> Callable[..., object]:
        """
        Get an async callable compatible with generic llm_model_func hooks.

        Returns:
            Callable that can be used as llm_model_func
        """
        return self._build_gateway_model_func(allow_multimodal=False)

    def get_vision_model_func(self) -> Callable[..., object]:
        """
        Get an async callable compatible with vision_model_func hooks.

        Returns:
            Callable that can be used as vision_model_func
        """
        return self._build_gateway_model_func(allow_multimodal=True)

    def supports_multimodal_images(self) -> bool:
        """Return whether the configured LLM can accept image input."""
        return supports_vision(getattr(self.config, "binding", "openai"), self.config.model)

    def _build_gateway_model_func(self, allow_multimodal: bool) -> Callable[..., object]:
        """Build integration hooks on top of :meth:`complete`."""

        def _resolve_messages(
            prompt: str,
            system_prompt: str | None,
            history_messages: list[dict[str, object]] | None,
            messages: list[dict[str, object]] | None,
        ) -> list[dict[str, Any]] | None:
            if messages:
                return cast(list[dict[str, Any]], messages)
            if not history_messages:
                return None

            full_messages: list[dict[str, Any]] = []
            if system_prompt and not (
                history_messages and history_messages[0].get("role") == "system"
            ):
                full_messages.append({"role": "system", "content": system_prompt})
            full_messages.extend(cast(list[dict[str, Any]], history_messages))
            if prompt:
                full_messages.append({"role": "user", "content": prompt})
            return full_messages or None

        async def model_func(
            prompt: str,
            system_prompt: str | None = None,
            history_messages: list[dict[str, object]] | None = None,
            image_data: str | None = None,
            messages: list[dict[str, object]] | None = None,
            **kwargs: object,
        ) -> str:
            payload_kwargs: dict[str, object] = dict(kwargs)

            # Normalize aliases from legacy callsites.
            payload_kwargs.pop("history_messages", None)
            payload_kwargs.pop("messages", None)
            payload_kwargs.pop("prompt", None)
            payload_kwargs.pop("system_prompt", None)

            default_system_prompt = system_prompt or "You are a helpful assistant."
            resolved_messages = _resolve_messages(
                prompt,
                default_system_prompt,
                history_messages,
                messages,
            )

            if allow_multimodal and image_data is not None:
                payload_kwargs["image_data"] = image_data

            return await self.complete(
                prompt=prompt,
                system_prompt=default_system_prompt,
                history=resolved_messages,
                **payload_kwargs,
            )

        return model_func


_client: LLMClient | None = None


def get_llm_client(config: LLMConfig | None = None) -> LLMClient:
    """
    Get or create the singleton LLM client.

    Args:
        config: Optional configuration. Only used on first call.

    Returns:
        LLMClient instance
    """
    global _client
    if _client is None:
        _client = LLMClient(config)
    return _client


def reset_llm_client() -> None:
    """Reset the singleton LLM client."""
    global _client
    _client = None
