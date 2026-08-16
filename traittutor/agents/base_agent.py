#!/usr/bin/env python
"""
Unified BaseAgent - Base class for all module agents.

This is the single source of truth for agent base functionality across:
- solve module
- research module
- question module (unified in Jan 2026 refactor)
"""

from abc import ABC, abstractmethod
import asyncio
from collections.abc import Mapping
import inspect
import logging
import time
from typing import Any, AsyncGenerator, Awaitable, Callable

from traittutor.config.settings import settings
from traittutor.gateway import GatewayAttachment, GatewayMessage, GatewayRequest, get_gateway
from traittutor.logging import LLMStats
from traittutor.services.config import get_agent_params
from traittutor.services.llm import get_llm_config
from traittutor.services.prompt import PromptLoadError, get_prompt_manager


class BaseAgent(ABC):
    """
    Unified base class for all module agents.

    This class provides:
    - LLM configuration management (api_key, base_url, model)
    - Agent parameters (temperature, max_tokens) from agents.yaml
    - Prompt loading via PromptManager
    - Unified LLM call interface
    - Token tracking (supports TokenTracker, LLMStats, or singleton tracker)
    - Logging

    Subclasses must implement the `process()` method.
    """

    # Shared LLMStats tracker for each module (class-level)
    _shared_stats: dict[str, LLMStats] = {}
    TraceCallback = Callable[[dict[str, Any]], Awaitable[None] | None]

    def __init__(
        self,
        module_name: str,
        agent_name: str,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        api_version: str | None = None,
        language: str = "zh",
        binding: str | None = None,
        config: dict[str, Any] | None = None,
        token_tracker: Any | None = None,
        log_dir: str | None = None,
    ):
        """
        Initialize base Agent.

        Args:
            module_name: Module name (solve/research/question)
            agent_name: Agent name (e.g., "solve_agent", "note_agent")
            api_key: API key (optional, defaults to environment variable)
            base_url: API endpoint (optional, defaults to environment variable)
            model: Model name (optional, defaults to environment variable)
            api_version: API version for Azure OpenAI (optional)
            language: Language setting ('zh' | 'en'), default 'zh'
            binding: Provider binding type (optional, defaults to 'openai')
            config: Optional configuration dictionary
            token_tracker: Optional external TokenTracker instance
            log_dir: Optional log directory path
        """
        self.module_name = module_name
        self.agent_name = agent_name
        self.language = language
        self._trace_callback: BaseAgent.TraceCallback | None = None
        self.api_key: str | None
        self.base_url: str | None
        self.model: str | None
        self.api_version: str | None
        self.binding: str
        # Ensure config is always a dict (not a dataclass like LLMConfig)
        if config is None:
            self.config = {}
        elif isinstance(config, dict):
            self.config = config
        else:
            # If config is a dataclass (like LLMConfig), convert to empty dict
            # The actual LLM config should be loaded via get_llm_config()
            self.config = {}

        # Load agent parameters from unified config (agents.yaml)
        self._agent_params = get_agent_params(module_name)

        # Load LLM configuration
        try:
            env_llm = get_llm_config()
            self.api_key = api_key or env_llm.api_key
            self.base_url = base_url or env_llm.base_url
            self.model = model or env_llm.model
            self.api_version = api_version or getattr(env_llm, "api_version", None)
            self.binding = str(binding or getattr(env_llm, "binding", None) or "openai")
        except Exception:
            self.api_key = api_key
            self.base_url = base_url
            self.model = model
            self.api_version = api_version
            self.binding = binding or "openai"

        # Get Agent-specific configuration (if config provided)
        self.agent_config = self.config.get("agents", {}).get(agent_name, {})
        llm_cfg = self.config.get("llm", {})
        # Ensure llm_config is always a dict (handle case where LLMConfig object is passed)
        if hasattr(llm_cfg, "__dataclass_fields__"):
            from dataclasses import asdict

            self.llm_config = asdict(llm_cfg)
        else:
            self.llm_config = llm_cfg if isinstance(llm_cfg, dict) else {}

        # Agent status
        self.enabled = self.agent_config.get("enabled", True)

        # Token tracker (external instance, optional)
        self.token_tracker = token_tracker

        # Initialize logger
        logger_name = f"{module_name.capitalize()}.{agent_name}"
        self.logger = logging.getLogger(f"traittutor.{logger_name}")

        # Load prompts using unified PromptManager
        try:
            self.prompts = get_prompt_manager().load_prompts(
                module_name=module_name,
                agent_name=agent_name,
                language=language,
            )
            if self.prompts:
                self.logger.debug(f"Prompts loaded: {agent_name} ({language})")
        except PromptLoadError:
            # Do not start an agent with no system prompt after a malformed or
            # missing prompt asset.  The caller can surface this configuration
            # fault instead of silently sending an under-specified LLM request.
            self.logger.exception("Prompt configuration failed for %s", agent_name)
            raise
        except Exception as e:
            self.prompts = None
            self.logger.warning(f"Failed to load prompts for {agent_name}: {e}")

    # -------------------------------------------------------------------------
    # Model and Parameter Getters
    # -------------------------------------------------------------------------

    def get_model(self) -> str:
        """
        Get model name.

        Priority: agent_config > llm_config > self.model > environment variable

        Returns:
            Model name

        Raises:
            ValueError: If model is not configured
        """
        # 1. Try agent-specific config
        if self.agent_config.get("model"):
            return self.agent_config["model"]

        # 2. Try general LLM config
        if self.llm_config.get("model"):
            return self.llm_config["model"]

        # 3. Use instance model
        if self.model:
            return self.model

        raise ValueError(
            f"Model not configured for agent {self.agent_name}. "
            "Please activate a model in Settings > Catalog."
        )

    def get_temperature(self) -> float:
        """
        Get temperature parameter from unified config (agents.yaml).

        Returns:
            Temperature value
        """
        return self._agent_params["temperature"]

    def get_max_tokens(self) -> int:
        """
        Get maximum token count from unified config (agents.yaml).

        Returns:
            Maximum token count
        """
        return self._agent_params["max_tokens"]

    def get_max_retries(self) -> int:
        """
        Get maximum retry count.

        Returns:
            Retry count
        """
        return self.agent_config.get("max_retries", settings.retry.max_retries)

    def refresh_config(self) -> None:
        """
        Refresh LLM configuration from the current active settings.

        This method reloads the LLM configuration from the unified config service,
        allowing agents to pick up configuration changes made by users in Settings
        without needing to restart the server or recreate the agent instance.

        Call this method before processing requests if you want to ensure
        the agent uses the latest user-configured LLM settings.
        """
        try:
            llm_config = get_llm_config()
            self.api_key = llm_config.api_key
            self.base_url = llm_config.base_url
            self.model = llm_config.model
            self.api_version = getattr(llm_config, "api_version", None)
            self.binding = str(getattr(llm_config, "binding", None) or "openai")
            self.logger.debug(
                f"Config refreshed: model={self.model}, base_url={self.base_url[:30]}..."
                if self.base_url
                else f"Config refreshed: model={self.model}"
            )
        except Exception as e:
            self.logger.warning(f"Failed to refresh config: {e}")

    def set_trace_callback(self, callback: TraceCallback | None) -> None:
        """Register a trace callback that receives structured LLM call events."""
        self._trace_callback = callback

    async def _emit_trace_event(self, payload: dict[str, Any]) -> None:
        callback = self._trace_callback
        if callback is None:
            return
        try:
            result = callback(payload)
            if inspect.isawaitable(result):
                await result
        except Exception as exc:
            self.logger.debug(f"Trace callback failed: {exc}")

    # -------------------------------------------------------------------------
    # Token Tracking
    # -------------------------------------------------------------------------

    @classmethod
    def get_stats(cls, module_name: str) -> LLMStats:
        """
        Get or create shared LLMStats tracker for a module.

        Args:
            module_name: Module name

        Returns:
            LLMStats instance
        """
        if module_name not in cls._shared_stats:
            cls._shared_stats[module_name] = LLMStats(module_name=module_name.capitalize())
        return cls._shared_stats[module_name]

    @classmethod
    def reset_stats(cls, module_name: str | None = None):
        """
        Reset shared stats.

        Args:
            module_name: Module name (if None, reset all)
        """
        if module_name:
            if module_name in cls._shared_stats:
                cls._shared_stats[module_name].reset()
        else:
            for stats in cls._shared_stats.values():
                stats.reset()

    @classmethod
    def print_stats(cls, module_name: str | None = None):
        """
        Print stats summary.

        Args:
            module_name: Module name (if None, print all)
        """
        if module_name:
            if module_name in cls._shared_stats:
                cls._shared_stats[module_name].print_summary()
        else:
            for stats in cls._shared_stats.values():
                stats.print_summary()

    def _track_tokens(
        self,
        model: str,
        system_prompt: str,
        user_prompt: str,
        response: str,
        stage: str | None = None,
    ):
        """
        Track token usage using available tracker.

        Supports:
        1. External TokenTracker (if self.token_tracker is set)
        2. Shared LLMStats (always available)

        Args:
            model: Model name
            system_prompt: System prompt
            user_prompt: User prompt
            response: LLM response
            stage: Stage name (optional)
        """
        stage_label = stage or self.agent_name

        # 1. Use external TokenTracker if provided
        if self.token_tracker:
            try:
                self.token_tracker.add_usage(
                    agent_name=self.agent_name,
                    stage=stage_label,
                    model=model,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    response_text=response,
                )
            except Exception:
                pass  # Don't let tracking errors affect main flow

        # 2. Always use shared LLMStats
        stats = self.get_stats(self.module_name)
        stats.add_call(
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response=response,
        )

    # -------------------------------------------------------------------------
    # LLM Call Interface
    # -------------------------------------------------------------------------

    async def call_llm(
        self,
        user_prompt: str,
        system_prompt: str,
        messages: list[dict[str, Any]] | None = None,
        response_format: dict[str, str] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        model: str | None = None,
        verbose: bool = True,
        stage: str | None = None,
        attachments: list[Any] | None = None,
        trace_meta: dict[str, Any] | None = None,
        timeout_seconds: float | None = None,
    ) -> str:
        """
        Unified interface for calling LLM (non-streaming).

        Uses the LLM factory to route calls to the appropriate provider
        (cloud or local) based on configuration.

        Args:
            user_prompt: User prompt (ignored if messages provided)
            system_prompt: System prompt (ignored if messages provided)
            messages: Pre-built messages array (optional, overrides prompt/system_prompt)
            response_format: Response format (e.g., {"type": "json_object"})
            temperature: Temperature parameter (optional, uses config by default)
            max_tokens: Maximum tokens (optional, uses config by default)
            model: Model name (optional, uses config by default)
            verbose: Whether to print raw LLM output (default True)
            stage: Stage marker for logging and tracking
            attachments: Image/file attachments for multimodal input (optional)
            timeout_seconds: Optional total timeout for the Gateway rollout path

        Returns:
            LLM response text
        """
        model = model or self.get_model()
        temperature = temperature if temperature is not None else self.get_temperature()
        max_tokens = max_tokens if max_tokens is not None else self.get_max_tokens()
        max_retries = self.get_max_retries()
        # Record call start time
        start_time = time.time()

        # Log input
        stage_label = stage or self.agent_name
        trace_payload_base = {
            "event": "llm_call",
            "state": "running",
            "agent_name": self.agent_name,
            "stage": stage_label,
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "streaming": False,
            **(trace_meta or {}),
        }
        await self._emit_trace_event(trace_payload_base)
        self.logger.debug(
            "LLM input %s:%s model=%s system_chars=%d user_chars=%d",
            self.agent_name,
            stage_label,
            model,
            len(system_prompt),
            len(user_prompt),
        )

        try:
            typed_messages = tuple(
                GatewayMessage.from_mapping(message) for message in (messages or [])
            ) or (
                GatewayMessage(role="system", content=system_prompt),
                GatewayMessage(role="user", content=user_prompt),
            )
            gateway_response = await get_gateway().complete(
                GatewayRequest(
                    prompt=user_prompt,
                    system_prompt=system_prompt,
                    purpose=f"agent:{self.module_name}:{self.agent_name}:{stage_label}",
                    messages=typed_messages,
                    attachments=tuple(attachments or []),
                    response_format=response_format,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    max_retries=max_retries,
                    timeout_seconds=timeout_seconds,
                    llm_config=self._gateway_llm_config(model),
                )
            )
            response = gateway_response.content
        except Exception as e:
            await self._emit_trace_event(
                {
                    **trace_payload_base,
                    "state": "error",
                    "response": str(e),
                }
            )
            self.logger.error(f"LLM call failed: {e}")
            raise

        # Calculate duration
        call_duration = time.time() - start_time

        # Track token usage
        self._track_tokens(
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response=response,
            stage=stage_label,
        )

        # Log output
        await self._emit_trace_event(
            {
                **trace_payload_base,
                "state": "complete",
                "response": response,
                "duration": call_duration,
            }
        )
        self.logger.debug(
            "LLM output %s:%s chars=%d duration=%.2fs",
            self.agent_name,
            stage_label,
            len(response),
            call_duration,
        )

        # Verbose output
        if verbose:
            self.logger.debug(f"LLM response: model={model}, duration={call_duration:.2f}s")

        return response

    def _gateway_llm_config(self, model: str):
        """Build the Gateway config from the same resolved agent overrides.

        Keeping the old model/provider/credential selection here means the
        feature flag changes only the audited facade, not which configured
        provider or token budget a BaseAgent receives.
        """
        current = get_llm_config()
        return current.model_copy(
            {
                "model": model,
                "api_key": self.api_key if self.api_key is not None else current.api_key,
                "base_url": self.base_url if self.base_url is not None else current.base_url,
                "effective_url": (
                    self.base_url if self.base_url is not None else current.effective_url
                ),
                "api_version": (
                    self.api_version if self.api_version is not None else current.api_version
                ),
                "binding": self.binding or current.binding,
                "provider_name": self.binding or current.provider_name,
            }
        )

    @staticmethod
    def _gateway_attachments(
        attachments: list[Any] | tuple[Any, ...] | None,
    ) -> tuple[GatewayAttachment, ...]:
        """Normalize legacy Agent image objects at the typed Gateway boundary."""

        normalized: list[GatewayAttachment] = []
        for attachment in attachments or ():
            if isinstance(attachment, GatewayAttachment):
                normalized.append(attachment)
                continue
            if isinstance(attachment, Mapping):
                payload = attachment
            else:
                payload = {
                    "type": getattr(attachment, "type", ""),
                    "filename": getattr(attachment, "filename", "image"),
                    "mime_type": getattr(attachment, "mime_type", "image/png"),
                    "base64": getattr(attachment, "base64", None),
                    "url": getattr(attachment, "url", None),
                }
            candidate = GatewayAttachment.from_mapping(payload)
            if candidate is not None:
                normalized.append(candidate)
        return tuple(normalized)

    async def stream_llm(
        self,
        user_prompt: str,
        system_prompt: str,
        messages: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        model: str | None = None,
        response_format: dict[str, Any] | None = None,
        stage: str | None = None,
        attachments: list[Any] | None = None,
        trace_meta: dict[str, Any] | None = None,
        verbose: bool = True,
        timeout_seconds: float | None = None,
    ) -> AsyncGenerator[str, None]:
        """
        Unified interface for streaming LLM responses.

        Uses the typed Gateway stream so retry, route rotation, deadlines,
        cancellation, telemetry, and provider capability gating stay centralized.

        Args:
            user_prompt: User prompt (ignored if messages provided)
            system_prompt: System prompt (ignored if messages provided)
            messages: Pre-built messages array (optional, overrides prompt/system_prompt)
            temperature: Temperature parameter (optional, uses config by default)
            max_tokens: Maximum tokens (optional, uses config by default)
            model: Model name (optional, uses config by default)
            response_format: JSON schema for structured output (optional)
            stage: Stage marker for logging
            attachments: Image/file attachments for multimodal input (optional)
            timeout_seconds: Optional total timeout for the Gateway stream

        Yields:
            Response chunks as strings
        """
        model = model or self.get_model()
        temperature = temperature if temperature is not None else self.get_temperature()
        max_tokens = max_tokens if max_tokens is not None else self.get_max_tokens()
        max_retries = self.get_max_retries()

        # Log input
        stage_label = stage or self.agent_name
        trace_payload_base = {
            "event": "llm_call",
            "state": "running",
            "agent_name": self.agent_name,
            "stage": stage_label,
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "streaming": True,
            **(trace_meta or {}),
        }
        await self._emit_trace_event(trace_payload_base)
        self.logger.debug(
            "LLM stream input %s:%s model=%s system_chars=%d user_chars=%d",
            self.agent_name,
            stage_label,
            model,
            len(system_prompt),
            len(user_prompt),
        )

        # Track start time
        start_time = time.time()
        full_response = ""

        try:
            typed_messages = tuple(
                GatewayMessage.from_mapping(message) for message in (messages or [])
            ) or (
                GatewayMessage(role="system", content=system_prompt),
                GatewayMessage(role="user", content=user_prompt),
            )
            request = GatewayRequest(
                prompt=user_prompt,
                system_prompt=system_prompt,
                purpose=f"agent:{self.module_name}:{self.agent_name}:{stage_label}",
                messages=typed_messages,
                attachments=self._gateway_attachments(attachments),
                response_format=response_format,
                temperature=temperature,
                max_tokens=max_tokens,
                max_retries=max_retries,
                timeout_seconds=timeout_seconds,
                llm_config=self._gateway_llm_config(model),
            )
            saw_terminal = False
            async for event in get_gateway().stream(request):
                if saw_terminal:
                    raise RuntimeError("Gateway emitted an event after the terminal receipt.")
                if event.type == "text" and event.text:
                    full_response += event.text
                    await self._emit_trace_event(
                        {
                            **trace_payload_base,
                            "state": "streaming",
                            "chunk": event.text,
                        }
                    )
                    yield event.text
                elif event.type in {"reasoning", "usage"}:
                    # BaseAgent remains a text-only facade. Reasoning and provider
                    # usage stay inside Gateway; legacy token accounting below is
                    # retained for callers that supplied their own tracker.
                    continue
                elif event.type == "final":
                    if event.finish_reason == "error":
                        raise RuntimeError("Gateway stream finished with an error status.")
                    saw_terminal = True
                elif event.type == "cancelled":
                    raise asyncio.CancelledError
                elif event.type == "tool_call":
                    raise RuntimeError(
                        "BaseAgent.stream_llm cannot consume Gateway tool-call events."
                    )

            if not saw_terminal:
                raise RuntimeError("Gateway stream ended without a terminal receipt.")

            # Track token usage after streaming completes
            self._track_tokens(
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                response=full_response,
                stage=stage_label,
            )

            # Log output
            call_duration = time.time() - start_time
            await self._emit_trace_event(
                {
                    **trace_payload_base,
                    "state": "complete",
                    "response": full_response,
                    "duration": call_duration,
                }
            )
            self.logger.debug(
                "LLM stream output %s:%s chars=%d duration=%.2fs",
                self.agent_name,
                stage_label,
                len(full_response),
                call_duration,
            )

        except Exception as e:
            await self._emit_trace_event(
                {
                    **trace_payload_base,
                    "state": "error",
                    "response": str(e),
                }
            )
            self.logger.error(f"LLM streaming failed: {e}")
            raise

    # -------------------------------------------------------------------------
    # Prompt Helpers
    # -------------------------------------------------------------------------

    def get_prompt(
        self,
        section_or_type: str = "system",
        field_or_fallback: str | None = None,
        fallback: str = "",
    ) -> str | None:
        """
        Get prompt by type or section/field.

        Supports two calling patterns:
        1. get_prompt("system") - simple key lookup
        2. get_prompt("section", "field", "fallback") - nested lookup (for research module)

        Args:
            section_or_type: Prompt type key or section name
            field_or_fallback: Field name (if nested) or fallback value (if simple)
            fallback: Fallback value if prompt not found (only used in nested mode)

        Returns:
            Prompt string or fallback
        """
        if not self.prompts:
            return (
                fallback
                if fallback
                else (
                    field_or_fallback
                    if isinstance(field_or_fallback, str) and field_or_fallback
                    else None
                )
            )

        # Check if this is a nested lookup (section.field pattern)
        # If field_or_fallback is provided and section_or_type points to a dict, use nested lookup
        section_value = self.prompts.get(section_or_type)

        if isinstance(section_value, dict) and field_or_fallback is not None:
            # Nested lookup: get_prompt("section", "field", "fallback")
            result = section_value.get(field_or_fallback)
            if result is not None:
                return result
            return fallback if fallback else None
        else:
            # Simple lookup: get_prompt("key") or get_prompt("key", "fallback")
            if section_value is not None:
                return section_value
            # field_or_fallback acts as fallback in simple mode
            return field_or_fallback if field_or_fallback else (fallback if fallback else None)

    def has_prompts(self) -> bool:
        """Check if prompts have been loaded."""
        return self.prompts is not None

    # -------------------------------------------------------------------------
    # Status
    # -------------------------------------------------------------------------

    def is_enabled(self) -> bool:
        """
        Check if Agent is enabled.

        Returns:
            Whether enabled
        """
        return self.enabled

    # -------------------------------------------------------------------------
    # Abstract Method
    # -------------------------------------------------------------------------

    @abstractmethod
    async def process(self, *args, **kwargs) -> Any:
        """
        Main processing logic of Agent (must be implemented by subclasses).

        Returns:
            Processing result
        """

    # -------------------------------------------------------------------------
    # String Representation
    # -------------------------------------------------------------------------

    def __repr__(self) -> str:
        """String representation of Agent."""
        return (
            f"{self.__class__.__name__}("
            f"module={self.module_name}, "
            f"name={self.agent_name}, "
            f"enabled={self.enabled})"
        )


__all__ = ["BaseAgent"]
