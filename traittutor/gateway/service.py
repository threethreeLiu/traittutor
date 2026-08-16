"""One audited, typed model boundary for product-facing LLM calls.

The Gateway owns bounded completion and streaming route policies. Tool loops
remain caller-owned and consume only typed tool-call events.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping, Sequence
import contextlib
from dataclasses import dataclass, field, replace
import json
import logging
import time
from typing import Any, Literal
import uuid

from traittutor.config.settings import settings
from traittutor.gateway.quota_rotation import (
    MAX_QUOTA_ATTEMPTS_PER_ROUTE,
    QuotaRotationExhaustedError,
    QuotaRotationPolicy,
    default_same_route_retryable,
    enumerate_fallback_routes,
)
from traittutor.gateway.route_health import create_configured_route_health_store
from traittutor.services.llm.capabilities import (
    supports_response_format,
    supports_tools,
    supports_vision,
)
from traittutor.services.llm.client import LLMClient
from traittutor.services.llm.config import LLMConfig, get_llm_config, get_token_limit_kwargs
from traittutor.services.llm.error_mapping import map_error
from traittutor.services.llm.multimodal import prepare_multimodal_messages
from traittutor.services.llm.provider_factory import get_runtime_provider
from traittutor.telemetry import (
    ProductEventSink,
    get_configured_product_event_sink,
    record_product_event,
)
from traittutor.telemetry.pricing import TokenPricing, create_configured_token_pricing

logger = logging.getLogger(__name__)


def _optional_string(value: Any) -> str | None:
    """Keep only non-empty strings from an untyped boundary input."""
    return value if isinstance(value, str) and value else None


def _usage_total(usage: Mapping[str, int]) -> int | None:
    """Return a non-negative provider token total without trusting extra keys."""
    total = usage.get("total_tokens")
    if isinstance(total, int) and not isinstance(total, bool) and total >= 0:
        return total
    for input_key, output_key in (
        ("prompt_tokens", "completion_tokens"),
        ("input_tokens", "output_tokens"),
    ):
        prompt = usage.get(input_key)
        completion = usage.get(output_key)
        # Chained isinstance (not ``all()``) so mypy narrows each variable
        # past the generator scope; bool is rejected because it passes int.
        if (
            isinstance(prompt, int)
            and not isinstance(prompt, bool)
            and prompt >= 0
            and isinstance(completion, int)
            and not isinstance(completion, bool)
            and completion >= 0
        ):
            return prompt + completion
    return None


GatewayRole = Literal["system", "user", "assistant", "tool"]
GatewayStreamEventType = Literal[
    "text",
    "reasoning",
    "tool_call",
    "usage",
    "final",
    "cancelled",
]

# A Gateway request may shorten a deadline for an interactive surface, but it
# may not turn a server process into an unbounded provider connection.  This
# is intentionally local to the server boundary rather than a client option.
_MAX_STREAM_TIMEOUT_SECONDS = 300.0
_FIRST_STREAM_OUTPUT_TIMEOUT_METADATA_KEY = "_gateway_first_stream_output_timeout_seconds"
_TOTAL_STREAM_TIMEOUT_METADATA_KEY = "_gateway_total_stream_timeout_seconds"


@dataclass(frozen=True)
class GatewayContentPart:
    """A provider-neutral text or image input part.

    The fields deliberately carry request data only.  They never appear in
    operational telemetry or the receipt returned to callers.
    """

    type: Literal["text", "image_url", "image"]
    text: str | None = None
    image_url: str | None = None
    source: Mapping[str, Any] | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "GatewayContentPart":
        """Normalize an OpenAI/Anthropic compatible content part."""
        part_type = str(value.get("type") or "").strip()
        if part_type == "text":
            text = value.get("text")
            if not isinstance(text, str):
                raise ValueError("Gateway text content parts require a string 'text'")
            return cls(type="text", text=text)
        if part_type == "image_url":
            raw_image_url = value.get("image_url")
            if isinstance(raw_image_url, Mapping):
                raw_image_url = raw_image_url.get("url")
            if not isinstance(raw_image_url, str) or not raw_image_url:
                raise ValueError("Gateway image_url parts require a URL")
            return cls(type="image_url", image_url=raw_image_url)
        if part_type == "image":
            source = value.get("source")
            if not isinstance(source, Mapping):
                raise ValueError("Gateway image parts require a source mapping")
            return cls(type="image", source=dict(source))
        raise ValueError(f"Unsupported Gateway content part type: {part_type!r}")

    def to_provider(self) -> dict[str, Any]:
        """Return the narrow provider shape consumed by the factory adapters."""
        if self.type == "text":
            return {"type": "text", "text": self.text or ""}
        if self.type == "image_url":
            return {"type": "image_url", "image_url": {"url": self.image_url or ""}}
        return {"type": "image", "source": dict(self.source or {})}


def _json_object(arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Return a JSON-safe object without accepting lossy tool arguments.

    A prior assistant tool call is replayed to a provider in a later request.
    Do the JSON round-trip at this boundary so a caller cannot accidentally
    turn a Python-only value, NaN, or a non-object JSON value into a different
    tool invocation on the second provider turn.
    """
    if not isinstance(arguments, Mapping):
        raise ValueError("Gateway tool call arguments must be a JSON object mapping")
    if any(not isinstance(key, str) for key in arguments):
        raise ValueError("Gateway tool call argument keys must be strings")
    try:
        encoded = json.dumps(
            dict(arguments),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        decoded = json.loads(encoded)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("Gateway tool call arguments must be JSON-safe") from exc
    if not isinstance(decoded, dict):
        raise ValueError("Gateway tool call arguments must encode a JSON object")
    return decoded


@dataclass(frozen=True)
class GatewayToolCall:
    """A provider-neutral function call for a caller-owned subsequent turn.

    ``arguments`` remains structured in process for a server-owned executor,
    and is converted to canonical JSON only when this call is replayed in an
    assistant message. Receipts and telemetry never include this value.
    """

    id: str
    name: str
    arguments: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("Gateway tool calls require a non-empty id")
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("Gateway tool calls require a non-empty name")
        object.__setattr__(self, "arguments", _json_object(self.arguments))

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "GatewayToolCall":
        """Normalize an OpenAI-compatible assistant ``tool_calls`` item."""
        raw_id = value.get("id")
        function = value.get("function")
        if not isinstance(raw_id, str) or not raw_id.strip():
            raise ValueError("Gateway assistant tool_calls require a non-empty id")
        if not isinstance(function, Mapping):
            raise ValueError("Gateway assistant tool_calls require a function mapping")
        raw_name = function.get("name")
        if not isinstance(raw_name, str) or not raw_name.strip():
            raise ValueError("Gateway assistant tool_calls require a non-empty function name")
        raw_arguments = function.get("arguments")
        if not isinstance(raw_arguments, str):
            raise ValueError("Gateway assistant tool_calls require JSON string arguments")
        try:
            arguments = json.loads(raw_arguments)
        except json.JSONDecodeError as exc:
            raise ValueError("Gateway assistant tool_call arguments must be valid JSON") from exc
        if not isinstance(arguments, Mapping):
            raise ValueError("Gateway assistant tool_call arguments must be a JSON object")
        return cls(id=raw_id.strip(), name=raw_name.strip(), arguments=arguments)

    def to_provider(self) -> dict[str, Any]:
        """Return the standard function-call envelope for a provider replay."""
        arguments = json.dumps(
            dict(self.arguments),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.name, "arguments": arguments},
        }


@dataclass(frozen=True)
class GatewayMessage:
    """A typed chat message accepted by :class:`TraitTutorGateway`."""

    role: GatewayRole
    content: str | tuple[GatewayContentPart, ...] | None
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: tuple[GatewayToolCall, ...] = ()

    def __post_init__(self) -> None:
        if self.role not in {"system", "user", "assistant", "tool"}:
            raise ValueError(f"Unsupported Gateway message role: {self.role!r}")
        if self.content is None and (self.role != "assistant" or not self.tool_calls):
            raise ValueError(
                "Gateway message content may be None only for an assistant tool-call message"
            )
        if self.content is not None and not isinstance(self.content, (str, tuple)):
            raise ValueError("Gateway message content must be text, content parts, or None")
        if not isinstance(self.tool_calls, tuple) or any(
            not isinstance(tool_call, GatewayToolCall) for tool_call in self.tool_calls
        ):
            raise ValueError("Gateway message tool_calls must be GatewayToolCall values")
        if self.tool_calls and self.role != "assistant":
            raise ValueError("Only assistant Gateway messages may contain tool_calls")
        if self.tool_call_id is not None and self.role != "tool":
            raise ValueError("Only tool Gateway messages may contain tool_call_id")
        if self.role == "tool" and (
            not isinstance(self.tool_call_id, str) or not self.tool_call_id.strip()
        ):
            raise ValueError("Gateway tool messages require a non-empty tool_call_id")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "GatewayMessage":
        """Normalize legacy message dictionaries at the Gateway boundary."""
        raw_role = str(value.get("role") or "").strip()
        if raw_role not in {"system", "user", "assistant", "tool"}:
            raise ValueError(f"Unsupported Gateway message role: {raw_role!r}")
        raw_content = value.get("content", "")
        if raw_content is None:
            content: str | tuple[GatewayContentPart, ...] | None = None
        elif isinstance(raw_content, str):
            content = raw_content
        elif isinstance(raw_content, Sequence) and not isinstance(raw_content, (str, bytes)):
            parts: list[GatewayContentPart] = []
            for part in raw_content:
                if not isinstance(part, Mapping):
                    raise ValueError("Gateway message content parts must be mappings")
                parts.append(GatewayContentPart.from_mapping(part))
            content = tuple(parts)
        else:
            raise ValueError("Gateway message content must be text or content parts")
        name = value.get("name")
        tool_call_id = value.get("tool_call_id")
        raw_tool_calls = value.get("tool_calls", ())
        if not isinstance(raw_tool_calls, Sequence) or isinstance(raw_tool_calls, (str, bytes)):
            raise ValueError("Gateway message tool_calls must be a sequence")
        tool_calls: list[GatewayToolCall] = []
        for tool_call in raw_tool_calls:
            if not isinstance(tool_call, Mapping):
                raise ValueError("Gateway assistant tool_calls must be mappings")
            tool_calls.append(GatewayToolCall.from_mapping(tool_call))
        return cls(
            role=raw_role,  # type: ignore[arg-type]
            content=content,
            name=name if isinstance(name, str) else None,
            tool_call_id=tool_call_id if isinstance(tool_call_id, str) else None,
            tool_calls=tuple(tool_calls),
        )

    def to_provider(self) -> dict[str, Any]:
        """Return a provider-compatible message without mutating caller input."""
        content: str | list[dict[str, Any]] | None
        if self.content is None:
            content = None
        elif isinstance(self.content, str):
            content = self.content
        else:
            content = [part.to_provider() for part in self.content]
        message: dict[str, Any] = {"role": self.role, "content": content}
        if self.name:
            message["name"] = self.name
        if self.tool_call_id:
            message["tool_call_id"] = self.tool_call_id
        if self.tool_calls:
            message["tool_calls"] = [tool_call.to_provider() for tool_call in self.tool_calls]
        return message


@dataclass(frozen=True)
class GatewayAttachment:
    """Normalized image attachment for the existing multimodal adapter."""

    type: Literal["image"]
    filename: str = "image"
    mime_type: str = "image/png"
    base64: str | None = None
    url: str | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "GatewayAttachment | None":
        """Create a typed image attachment from the internal chat payload."""
        if str(value.get("type", "") or "") != "image":
            return None
        base64_data = _optional_string(value.get("base64"))
        url = _optional_string(value.get("url"))
        if not base64_data and not url:
            return None
        filename = value.get("filename", "image")
        mime_type = value.get("mime_type", "image/png")
        return cls(
            type="image",
            filename=filename if isinstance(filename, str) and filename else "image",
            mime_type=mime_type if isinstance(mime_type, str) and mime_type else "image/png",
            base64=base64_data,
            url=url,
        )


@dataclass(frozen=True)
class GatewayTool:
    """A normalized function-tool schema; execution stays outside this slice."""

    name: str
    description: str = ""
    parameters: Mapping[str, Any] = field(default_factory=lambda: {"type": "object"})

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "GatewayTool":
        """Accept the OpenAI envelope or a concise internal function schema."""
        function = value.get("function") if value.get("type") == "function" else value
        if not isinstance(function, Mapping):
            raise ValueError("Gateway tools require a function mapping")
        name = function.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Gateway tools require a non-empty function name")
        description = function.get("description", "")
        parameters = function.get("parameters", {"type": "object"})
        if not isinstance(parameters, Mapping):
            raise ValueError("Gateway tool parameters must be a mapping")
        normalized_parameters = dict(parameters)
        normalized_parameters.setdefault("type", "object")
        return cls(
            name=name.strip(),
            description=description if isinstance(description, str) else "",
            parameters=normalized_parameters,
        )

    def to_provider(self) -> dict[str, Any]:
        """Return the standard function-tool envelope."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": dict(self.parameters),
            },
        }


@dataclass(frozen=True)
class GatewayReceipt:
    """Safe operational summary returned with a completion.

    This is deliberately redacted: no prompt, message/content part, attachment
    URL/data, metadata, user identifier, endpoint, header, or credential can
    enter this value.
    """

    request_id: str
    purpose: str
    model: str
    provider: str
    route: str
    latency_ms: int
    timeout_seconds: float | None
    response_format_applied: bool
    tools_applied: int
    attachments_applied: int


@dataclass(frozen=True)
class GatewayStreamEvent:
    """One typed event from :meth:`TraitTutorGateway.stream`.

    A successful stream ends with exactly one ``final`` event containing a
    redacted receipt.  A cooperative cancellation ends with ``cancelled``;
    provider failures continue to raise so existing error handling does not
    mistake an incomplete answer for successful model output.
    """

    type: GatewayStreamEventType
    text: str | None = None
    tool_call: GatewayToolCall | None = None
    usage: Mapping[str, int] | None = None
    finish_reason: str | None = None
    receipt: GatewayReceipt | None = None

    def __post_init__(self) -> None:
        if self.type in {"text", "reasoning"} and not self.text:
            raise ValueError(f"Gateway {self.type} events require non-empty text")
        if self.type == "tool_call" and self.tool_call is None:
            raise ValueError("Gateway tool_call events require a tool_call")
        if self.type == "usage" and self.usage is None:
            raise ValueError("Gateway usage events require usage")
        if self.type in {"final", "cancelled"} and self.receipt is None:
            raise ValueError(f"Gateway {self.type} events require a receipt")


@dataclass(frozen=True)
class GatewayRequest:
    prompt: str
    system_prompt: str
    purpose: str
    user_id: str | None = None
    messages: tuple[GatewayMessage, ...] = ()
    attachments: tuple[GatewayAttachment, ...] = ()
    tools: tuple[GatewayTool, ...] = ()
    reasoning_effort: str | None = None
    response_format: Mapping[str, Any] | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    max_retries: int | None = None
    timeout_seconds: float | None = None
    # Diagnostics and other exact-route probes must observe the selected route
    # itself rather than succeeding through a catalog fallback.
    allow_route_fallback: bool = True
    # Opt OUT of the gateway's bounded quota-rotation wrapper. Only callers
    # that already run under their own bounded rotation policy (generation's
    # ``GenerationRoutePolicy``) may set this to False — the exemption is an
    # explicit typed declaration, never a purpose-string prefix convention
    # that any caller could silently claim.
    allow_quota_rotation: bool = True
    # Request image output modalities from a chat-completions model. The
    # provider transport adds ``modalities: ["image", "text"]`` and the
    # response carries raw image parts on ``GatewayResponse.images``. Only
    # the imagegen service uses this today; ordinary text callers leave it
    # off so nothing about their requests changes.
    image_modalities: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    # Generation can select a configured alternate route without changing the
    # user's global Settings selection.  The config never leaves the server.
    llm_config: LLMConfig | None = None
    # This is an in-process control supplied by a server handler, never a
    # client payload. It lets a disconnect stop the provider work promptly.
    cancellation_event: asyncio.Event | None = field(default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        if any(not isinstance(item, GatewayMessage) for item in self.messages):
            raise ValueError("GatewayRequest messages must be GatewayMessage values")
        if any(not isinstance(item, GatewayAttachment) for item in self.attachments):
            raise ValueError("GatewayRequest attachments must be GatewayAttachment values")
        if any(not isinstance(item, GatewayTool) for item in self.tools):
            raise ValueError("GatewayRequest tools must be GatewayTool values")


@dataclass(frozen=True)
class GatewayResponse:
    request_id: str
    content: str
    model: str
    purpose: str
    latency_ms: int
    receipt: GatewayReceipt
    # Provider-core token counters are server operational data. Callers may
    # audit them, but receipts/browser projections continue to omit usage.
    usage: Mapping[str, int] = field(default_factory=dict)
    # Raw image parts (``{"url": ...}``) for image-modality requests; empty
    # for every ordinary text completion.
    images: tuple[Mapping[str, Any], ...] = ()


@dataclass(frozen=True)
class PreparedGatewayCompletion:
    """Private normalized provider call; never serialized or logged."""

    config: LLMConfig
    messages: list[dict[str, Any]]
    kwargs: dict[str, Any]
    response_format_applied: bool
    tools_applied: int
    attachments_applied: int


class TraitTutorGateway:
    """Gateway facade that keeps credentials and provider details server-side."""

    def __init__(
        self,
        *,
        event_sink: ProductEventSink | None = None,
        token_pricing: TokenPricing | None = None,
    ) -> None:
        self._event_sink = (
            event_sink if event_sink is not None else get_configured_product_event_sink()
        )
        self._token_pricing = (
            token_pricing if token_pricing is not None else create_configured_token_pricing()
        )

    async def complete(self, request: GatewayRequest) -> GatewayResponse:
        """Run one typed, non-streaming completion through the shared provider path.

        Optional provider features are capability-gated here.  An unsupported
        structured-output or tool request falls back to the plain completion
        rather than failing the user request.  Tool *execution* is explicitly
        out of scope: this method returns text exactly like the legacy facade.
        """
        request_id = str(uuid.uuid4())
        started = time.monotonic()
        prepared: PreparedGatewayCompletion | None = None
        try:
            prepared = self._prepare_completion(request)
            if self._quota_rotation_applies(request):
                content, usage, images, prepared = await self._run_with_quota_rotation(
                    request, prepared
                )
            else:
                content, usage, images = await self._run_completion(request, prepared)
        except QuotaRotationExhaustedError as exc:
            latency_ms = int((time.monotonic() - started) * 1000)
            if exc.last_config is not None:
                prepared = self._prepare_for_route(request, exc.last_config)
            self._record_failure(request_id, request, prepared, exc, latency_ms)
            if exc.last_error is not None:
                # Preserve the real provider error so callers keep their
                # existing handling instead of seeing a generic exhaustion type.
                raise exc.last_error from exc
            raise
        except Exception as exc:
            latency_ms = int((time.monotonic() - started) * 1000)
            self._record_failure(request_id, request, prepared, exc, latency_ms)
            raise
        latency_ms = int((time.monotonic() - started) * 1000)
        receipt = self._receipt(request_id, request, prepared, latency_ms)
        self._record_success(request_id, request, prepared.config, latency_ms, usage)
        return GatewayResponse(
            request_id=request_id,
            content=content,
            model=prepared.config.model,
            purpose=request.purpose,
            latency_ms=latency_ms,
            receipt=receipt,
            usage=usage,
            images=tuple(images),
        )

    async def stream(self, request: GatewayRequest) -> AsyncIterator[GatewayStreamEvent]:
        """Yield typed provider output without flattening tool calls into text.

        The typed stream preserves final provider tool calls for caller-owned
        loops. Consumers explicitly project the event types they support and
        reject incomplete terminal states.
        """
        if self._quota_rotation_applies(request):
            async for event in self._stream_with_quota_rotation(request):
                yield event
            return
        async for event in self._stream_one_route(request):
            yield event

    async def stream_text(
        self,
        request: GatewayRequest,
        *,
        include_reasoning: bool = False,
    ) -> AsyncIterator[str]:
        """Project a typed stream onto the legacy text-only Agent contract.

        This adapter deliberately lives on the Gateway so callers cannot
        rebuild provider retry or fallback loops. Optional reasoning is
        delimited for the existing ``clean_thinking_tags`` consumers.
        """
        saw_terminal = False
        reasoning_open = False
        async for event in self.stream(request):
            if saw_terminal:
                raise RuntimeError("Gateway emitted an event after the terminal receipt.")
            if event.type == "reasoning":
                if include_reasoning and event.text:
                    if not reasoning_open:
                        reasoning_open = True
                        yield "<think>"
                    yield event.text
                continue
            if event.type == "text":
                if reasoning_open:
                    reasoning_open = False
                    yield "</think>"
                if event.text:
                    yield event.text
                continue
            if event.type == "usage":
                continue
            if event.type == "tool_call":
                raise RuntimeError("Gateway text projection cannot consume tool-call events.")
            if event.type == "cancelled":
                raise asyncio.CancelledError
            if event.type == "final":
                if reasoning_open:
                    reasoning_open = False
                    yield "</think>"
                if event.finish_reason == "error":
                    raise RuntimeError("Gateway stream finished with an error status.")
                saw_terminal = True

        if not saw_terminal:
            raise RuntimeError("Gateway stream ended without a terminal receipt.")

    async def _stream_one_route(self, request: GatewayRequest) -> AsyncIterator[GatewayStreamEvent]:
        """Yield one route's typed provider output without flattening tools."""
        request_id = str(uuid.uuid4())
        started = time.monotonic()
        prepared: PreparedGatewayCompletion | None = None
        run_task: asyncio.Task[None] | None = None
        cancellation_waiter: asyncio.Task[bool] | None = None
        emitted_terminal = False
        route_attempt_timed_out = False
        try:
            prepared = self._prepare_completion(request)
            timeout_seconds = self._stream_timeout_seconds(request.timeout_seconds)
            total_timeout_value = request.metadata.get(_TOTAL_STREAM_TIMEOUT_METADATA_KEY)
            receipt_timeout_seconds = (
                float(total_timeout_value)
                if isinstance(total_timeout_value, (int, float))
                and not isinstance(total_timeout_value, bool)
                and total_timeout_value > 0
                else timeout_seconds
            )
            first_output_timeout_value = request.metadata.get(
                _FIRST_STREAM_OUTPUT_TIMEOUT_METADATA_KEY
            )
            first_output_deadline = (
                time.monotonic() + float(first_output_timeout_value)
                if isinstance(first_output_timeout_value, (int, float))
                and not isinstance(first_output_timeout_value, bool)
                and first_output_timeout_value > 0
                else None
            )
            queue: asyncio.Queue[GatewayStreamEvent | BaseException | None] = asyncio.Queue()
            content_emitted = False
            replayable_output_emitted = False

            async def on_content_delta(text: str) -> None:
                nonlocal content_emitted
                if text:
                    content_emitted = True
                    await queue.put(GatewayStreamEvent(type="text", text=text))

            async def on_reasoning_delta(text: str) -> None:
                if text:
                    await queue.put(GatewayStreamEvent(type="reasoning", text=text))

            async def run_provider() -> None:
                nonlocal content_emitted
                try:
                    provider = get_runtime_provider(prepared.config)
                    kwargs = dict(prepared.kwargs)
                    retry_delays = self._stream_retry_delays(kwargs.pop("max_retries", None))
                    response = await asyncio.wait_for(
                        provider.chat_stream_with_retry(
                            messages=prepared.messages,
                            tools=kwargs.pop("tools", None),
                            model=prepared.config.model,
                            reasoning_effort=prepared.config.reasoning_effort,
                            on_content_delta=on_content_delta,
                            on_reasoning_delta=on_reasoning_delta,
                            retry_delays=retry_delays,
                            allow_image_fallback=not supports_vision(
                                prepared.config.binding, prepared.config.model
                            ),
                            **kwargs,
                        ),
                        timeout=timeout_seconds,
                    )
                    if response.finish_reason == "error":
                        raise map_error(
                            RuntimeError(response.content or "LLM stream failed"),
                            provider=prepared.config.provider_name,
                        )
                    if (
                        not content_emitted
                        and response.content
                        and response.content != response.reasoning_content
                    ):
                        content_emitted = True
                        await queue.put(GatewayStreamEvent(type="text", text=response.content))
                    for tool_call in response.tool_calls:
                        await queue.put(
                            GatewayStreamEvent(
                                type="tool_call",
                                tool_call=GatewayToolCall(
                                    id=str(tool_call.id),
                                    name=str(tool_call.name),
                                    arguments=dict(tool_call.arguments or {}),
                                ),
                            )
                        )
                    usage = self._stream_usage(response.usage)
                    if usage:
                        await queue.put(GatewayStreamEvent(type="usage", usage=usage))
                    latency_ms = int((time.monotonic() - started) * 1000)
                    receipt = self._receipt(
                        request_id,
                        request,
                        prepared,
                        latency_ms,
                        timeout_seconds=receipt_timeout_seconds,
                    )
                    self._record_stream_success(
                        request_id,
                        request,
                        prepared.config,
                        latency_ms,
                        usage,
                    )
                    await queue.put(
                        GatewayStreamEvent(
                            type="final",
                            finish_reason=response.finish_reason or "stop",
                            receipt=receipt,
                        )
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    latency_ms = int((time.monotonic() - started) * 1000)
                    self._record_stream_failure(request_id, request, prepared, exc, latency_ms)
                    await queue.put(exc)
                finally:
                    await queue.put(None)

            run_task = asyncio.create_task(run_provider())
            if request.cancellation_event is not None:
                cancellation_waiter = asyncio.create_task(request.cancellation_event.wait())

            while True:
                event_waiter = asyncio.create_task(queue.get())
                waiters: set[asyncio.Task[Any]] = {event_waiter}
                if cancellation_waiter is not None:
                    waiters.add(cancellation_waiter)
                first_output_wait = (
                    max(0.0, first_output_deadline - time.monotonic())
                    if first_output_deadline is not None and not replayable_output_emitted
                    else None
                )
                done, pending = await asyncio.wait(
                    waiters,
                    timeout=first_output_wait,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not done:
                    route_attempt_timed_out = True
                    event_waiter.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await event_waiter
                    raise TimeoutError(
                        "Gateway stream produced no output before the route-attempt deadline"
                    )
                if cancellation_waiter is not None and cancellation_waiter in done:
                    event_waiter.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await event_waiter
                    if run_task is not None and not run_task.done():
                        run_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await run_task
                    latency_ms = int((time.monotonic() - started) * 1000)
                    receipt = self._receipt(
                        request_id,
                        request,
                        prepared,
                        latency_ms,
                        timeout_seconds=receipt_timeout_seconds,
                    )
                    self._record_stream_cancelled(request_id, request, prepared.config, latency_ms)
                    emitted_terminal = True
                    yield GatewayStreamEvent(type="cancelled", receipt=receipt)
                    return
                for pending_waiter in pending:
                    if pending_waiter is not cancellation_waiter:
                        pending_waiter.cancel()
                item = event_waiter.result()
                if item is None:
                    return
                if isinstance(item, BaseException):
                    raise item
                if item.type in {"text", "reasoning", "tool_call"}:
                    replayable_output_emitted = True
                if item.type in {"final", "cancelled"}:
                    emitted_terminal = True
                yield item
        except asyncio.CancelledError:
            raise
        finally:
            if cancellation_waiter is not None and not cancellation_waiter.done():
                cancellation_waiter.cancel()
            if run_task is not None and not run_task.done():
                run_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await run_task
                if prepared is not None and not emitted_terminal and not route_attempt_timed_out:
                    latency_ms = int((time.monotonic() - started) * 1000)
                    self._record_stream_cancelled(request_id, request, prepared.config, latency_ms)

    async def _stream_with_quota_rotation(
        self, request: GatewayRequest
    ) -> AsyncIterator[GatewayStreamEvent]:
        """Apply bounded retry and fallback before any stream output.

        Each provider invocation has inner retries disabled. A transient failure
        gets one same-route retry; quota, credential, and other failures rotate
        immediately. Once text/reasoning/tool output reaches the consumer, the
        request becomes non-replayable and the real error surfaces unchanged.
        """
        primary = self._prepare_completion(request).config
        routes = enumerate_fallback_routes(primary, max_routes=2)
        route_health_store = create_configured_route_health_store()
        total_timeout = self._stream_timeout_seconds(request.timeout_seconds)
        deadline = time.monotonic() + total_timeout
        last_error: Exception | None = None
        attempt_ordinal = 0
        for route_index, route in enumerate(routes, start=1):
            for retry_index in range(1, MAX_QUOTA_ATTEMPTS_PER_ROUTE + 1):
                attempt_ordinal += 1
                attempt_started = time.monotonic()
                if not route_health_store.allows(route):
                    self._record_stream_route_attempt(
                        request=request,
                        route=route,
                        route_index=route_index,
                        retry_index=retry_index,
                        ordinal=attempt_ordinal,
                        outcome="circuit_open",
                        duration_ms=0,
                        timed_out=False,
                    )
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._record_stream_route_attempt(
                        request=request,
                        route=route,
                        route_index=route_index,
                        retry_index=retry_index,
                        ordinal=attempt_ordinal,
                        outcome="timeout",
                        duration_ms=0,
                        timed_out=True,
                    )
                    if last_error is not None:
                        raise last_error
                    raise QuotaRotationExhaustedError(
                        "stream route deadline exceeded",
                        last_config=route,
                    )
                remaining_route_count = len(routes) - route_index + 1
                # Reserve a viable share of the whole-stream deadline for each
                # configured route. Immediate failures can still use the normal
                # same-route retry, while a real stall cannot consume the
                # fallback route's entire budget.
                attempt_timeout = remaining / remaining_route_count
                route_request = replace(
                    request,
                    llm_config=route,
                    max_retries=0,
                    timeout_seconds=remaining,
                    metadata={
                        **request.metadata,
                        _FIRST_STREAM_OUTPUT_TIMEOUT_METADATA_KEY: attempt_timeout,
                        _TOTAL_STREAM_TIMEOUT_METADATA_KEY: total_timeout,
                    },
                )
                route_saw_output = False
                route_cancelled = False
                route_finished = False
                try:
                    async for event in self._stream_one_route(route_request):
                        if event.type in {"text", "reasoning", "tool_call"}:
                            route_saw_output = True
                        elif event.type == "cancelled":
                            route_cancelled = True
                            self._record_stream_route_attempt(
                                request=request,
                                route=route,
                                route_index=route_index,
                                retry_index=retry_index,
                                ordinal=attempt_ordinal,
                                outcome="cancelled",
                                duration_ms=int((time.monotonic() - attempt_started) * 1000),
                                timed_out=False,
                            )
                        elif event.type == "final":
                            route_finished = True
                            route_health_store.record_success(route)
                            self._record_stream_route_attempt(
                                request=request,
                                route=route,
                                route_index=route_index,
                                retry_index=retry_index,
                                ordinal=attempt_ordinal,
                                outcome="success",
                                duration_ms=int((time.monotonic() - attempt_started) * 1000),
                                timed_out=False,
                            )
                        yield event
                    if route_cancelled:
                        return
                    if not route_finished:
                        raise RuntimeError("Gateway stream route ended without a terminal receipt")
                    return
                except Exception as exc:
                    last_error = exc
                    route_health_store.record_failure(route)
                    timed_out = (
                        isinstance(exc, TimeoutError) or "timeout" in type(exc).__name__.lower()
                    )
                    self._record_stream_route_attempt(
                        request=request,
                        route=route,
                        route_index=route_index,
                        retry_index=retry_index,
                        ordinal=attempt_ordinal,
                        outcome="timeout" if timed_out else "error",
                        duration_ms=int((time.monotonic() - attempt_started) * 1000),
                        timed_out=timed_out,
                    )
                    if route_saw_output:
                        raise
                    if retry_index == 1 and default_same_route_retryable(exc):
                        logger.warning(
                            "gateway_stream_retrying model=%s error=%s",
                            route.model,
                            type(exc).__name__,
                        )
                        continue
                    logger.warning(
                        "gateway_stream_rotating model=%s error=%s",
                        route.model,
                        type(exc).__name__,
                    )
                    break
        if last_error is not None:
            raise last_error
        raise QuotaRotationExhaustedError("all quota rotation stream routes failed")

    def _record_stream_route_attempt(
        self,
        *,
        request: GatewayRequest,
        route: LLMConfig,
        route_index: int,
        retry_index: int,
        ordinal: int,
        outcome: str,
        duration_ms: int,
        timed_out: bool,
    ) -> None:
        """Emit the same payload-free route facts as non-streaming rotation."""

        record_product_event(
            self._event_sink,
            "gateway.route_attempt",
            {
                "purpose": request.purpose,
                "attempt": ordinal,
                "route_index": route_index,
                "retry_index": retry_index,
                "fallback_used": route_index > 1,
                "duration_ms": max(0, duration_ms),
                "outcome": outcome,
                "timed_out": timed_out,
                "degraded": False,
                "model": route.model,
                "provider": route.provider_name,
                "route": route.provider_name,
            },
        )

    @staticmethod
    def _stream_timeout_seconds(value: float | None) -> float:
        """Return a bounded whole-stream deadline from a server request."""
        if value is None:
            return _MAX_STREAM_TIMEOUT_SECONDS
        if value <= 0:
            raise ValueError("Gateway timeout_seconds must be greater than zero")
        return min(float(value), _MAX_STREAM_TIMEOUT_SECONDS)

    @staticmethod
    def _stream_retry_delays(requested_retries: int | None) -> tuple[float, ...]:
        """Use the server retry policy, allowing callers only to shorten it."""
        configured_retries = max(0, int(settings.retry.max_retries))
        retries = configured_retries
        if requested_retries is not None:
            retries = min(retries, max(0, requested_retries))
        base_delay = max(0.0, float(settings.retry.base_delay))
        return tuple(
            min(
                base_delay * (2**attempt) if settings.retry.exponential_backoff else base_delay,
                120.0,
            )
            for attempt in range(retries)
        )

    @staticmethod
    def _stream_usage(value: Any) -> dict[str, int]:
        """Copy only integer usage counters from an untrusted provider reply."""
        if not isinstance(value, Mapping):
            return {}
        return {
            str(key): count
            for key, count in value.items()
            if (
                isinstance(key, str)
                and isinstance(count, int)
                and not isinstance(count, bool)
                and count >= 0
            )
        }

    def _prepare_completion(self, request: GatewayRequest) -> PreparedGatewayCompletion:
        """Normalize optional inputs before invoking the provider client."""
        config = request.llm_config or get_llm_config()
        if request.reasoning_effort:
            config = config.model_copy({"reasoning_effort": request.reasoning_effort})
        if request.timeout_seconds is not None and request.timeout_seconds <= 0:
            raise ValueError("Gateway timeout_seconds must be greater than zero")

        attachments = self._normalized_attachments(request.attachments)
        messages = self._provider_messages(request)
        if attachments:
            messages = prepare_multimodal_messages(
                messages, list(attachments), binding=config.binding, model=config.model
            ).messages
        response_format = self._normalized_response_format(request.response_format)
        kwargs, tools_applied = self._provider_kwargs(request, config, response_format)
        return PreparedGatewayCompletion(
            config=config,
            messages=messages,
            kwargs=kwargs,
            response_format_applied="response_format" in kwargs,
            tools_applied=tools_applied,
            attachments_applied=len(attachments),
        )

    def _provider_kwargs(
        self,
        request: GatewayRequest,
        config: LLMConfig,
        response_format: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], int]:
        """Build only provider-supported optional completion arguments."""
        kwargs: dict[str, Any] = {}
        if response_format is not None and supports_response_format(config.binding, config.model):
            kwargs["response_format"] = response_format
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        if request.max_tokens is not None:
            kwargs.update(get_token_limit_kwargs(config.model, request.max_tokens))
        tools = self._normalized_tools(request.tools)
        if tools and supports_tools(config.binding, config.model):
            kwargs["tools"] = [tool.to_provider() for tool in tools]
        if request.max_retries is not None:
            kwargs["max_retries"] = request.max_retries
        if request.image_modalities:
            kwargs["modalities"] = ["image", "text"]
        return kwargs, len(tools) if "tools" in kwargs else 0

    @staticmethod
    async def _run_completion(
        request: GatewayRequest,
        prepared: PreparedGatewayCompletion,
    ) -> tuple[str, dict[str, int], list[dict[str, Any]]]:
        """Run one provider call, applying the Gateway's total deadline."""
        client = LLMClient(prepared.config)
        # The Gateway owns quota rotation for calls that reach it.  Mark the
        # call so the legacy factory does not wrap the same provider call with
        # its own rotation and multiply provider spend.
        kwargs = dict(prepared.kwargs)
        kwargs["_skip_quota_rotation"] = True
        completion = client.complete_with_usage(
            request.prompt,
            system_prompt=request.system_prompt,
            history=prepared.messages or None,
            **kwargs,
        )
        if request.timeout_seconds is None:
            return await completion
        return await asyncio.wait_for(completion, timeout=request.timeout_seconds)

    @staticmethod
    def _quota_rotation_applies(request: GatewayRequest) -> bool:
        """Return whether the quota rotation policy should wrap this completion.

        Callers that own a bounded rotation policy (generation's
        ``GenerationRoutePolicy``) opt out explicitly via
        ``allow_quota_rotation=False``; applying the general wrapper there
        would double-rotate the same request. The purpose string is purely
        informational — a ``generate:`` prefix used to silently exempt any
        caller that claimed it, which bypassed rotation for direct
        planner/specialist completions that have no policy of their own.
        """
        return request.allow_route_fallback and request.allow_quota_rotation

    def _prepare_for_route(
        self, request: GatewayRequest, config: LLMConfig
    ) -> PreparedGatewayCompletion:
        """Re-normalize a completion for a rotated server-selected config.

        ``LLMConfig`` is frozen, and capability gating (response format, tools)
        depends on the selected model, so each route needs its own prepared
        call rather than mutating the primary's one.
        """
        return self._prepare_completion(replace(request, llm_config=config))

    async def _run_with_quota_rotation(
        self,
        request: GatewayRequest,
        prepared: PreparedGatewayCompletion,
    ) -> tuple[str, dict[str, int], list[dict[str, Any]], PreparedGatewayCompletion]:
        """Run one completion through the bounded quota rotation policy.

        The policy issues one provider call per attempt with no inner retry
        (``max_retries=0``) and its own deadline, so the legacy client cannot
        re-introduce an unbounded retry envelope.  On success the winning
        route's prepared call is returned for receipt/capability accuracy.
        """
        primary = prepared.config

        async def invoke(
            route: LLMConfig, remaining: float
        ) -> tuple[str, dict[str, int], list[dict[str, Any]]]:
            route_request = replace(
                request,
                llm_config=route,
                max_retries=0,
                timeout_seconds=remaining,
            )
            return await self._run_completion(
                route_request, self._prepare_completion(route_request)
            )

        result = await QuotaRotationPolicy(
            purpose=request.purpose,
            event_sink=self._event_sink,
        ).run(primary, invoke=invoke, same_route_retryable=default_same_route_retryable)
        content, usage, images = result.value
        return content, usage, images, self._prepare_for_route(request, result.config)

    @staticmethod
    def _receipt(
        request_id: str,
        request: GatewayRequest,
        prepared: PreparedGatewayCompletion,
        latency_ms: int,
        *,
        timeout_seconds: float | None = None,
    ) -> GatewayReceipt:
        """Create the redacted receipt from server-derived operational fields."""
        config = prepared.config
        return GatewayReceipt(
            request_id=request_id,
            purpose=request.purpose,
            model=config.model,
            provider=config.provider_name,
            route=config.binding,
            latency_ms=latency_ms,
            timeout_seconds=(
                timeout_seconds if timeout_seconds is not None else request.timeout_seconds
            ),
            response_format_applied=prepared.response_format_applied,
            tools_applied=prepared.tools_applied,
            attachments_applied=prepared.attachments_applied,
        )

    def _record_failure(
        self,
        request_id: str,
        request: GatewayRequest,
        prepared: PreparedGatewayCompletion | None,
        error: Exception,
        latency_ms: int,
    ) -> None:
        """Emit a payload-free failure event without changing the raised error."""
        timed_out = isinstance(error, TimeoutError) or "timeout" in type(error).__name__.lower()
        attributes: dict[str, str | int | bool] = {
            "request_id": request_id,
            "purpose": request.purpose,
            "attempt": 1,
            "duration_ms": latency_ms,
            "outcome": "timeout" if timed_out else "error",
            "timed_out": timed_out,
            "degraded": False,
        }
        if prepared is not None:
            config = prepared.config
            attributes.update(
                model=config.model, provider=config.provider_name, route=config.binding
            )
        record_product_event(self._event_sink, "gateway.complete", attributes)

    def _record_success(
        self,
        request_id: str,
        request: GatewayRequest,
        config: LLMConfig,
        latency_ms: int,
        usage: Mapping[str, int],
    ) -> None:
        """Audit a successful completion with derived, non-sensitive fields only."""
        logger.info(
            "gateway_complete request_id=%s purpose=%s model=%s latency_ms=%s",
            request_id,
            request.purpose,
            config.model,
            latency_ms,
        )
        try:
            from traittutor.multi_user.audit import log_usage

            log_usage(
                "model",
                config.model,
                "gateway_complete",
                {"purpose": request.purpose, "latency_ms": latency_ms},
            )
        except Exception:
            pass
        attributes: dict[str, str | int | bool] = {
            "request_id": request_id,
            "purpose": request.purpose,
            "model": config.model,
            "provider": config.provider_name,
            "route": config.binding,
            "attempt": 1,
            "duration_ms": latency_ms,
            "outcome": "succeeded",
            "timed_out": False,
            "degraded": False,
        }
        token_total = _usage_total(usage)
        if token_total is not None:
            attributes["total_tokens"] = token_total
        cost_picousd = self._priced_cost_picousd(config.model, usage)
        if cost_picousd is not None:
            attributes["cost_picousd"] = cost_picousd
        record_product_event(
            self._event_sink,
            "gateway.complete",
            attributes,
        )

    def _record_stream_success(
        self,
        request_id: str,
        request: GatewayRequest,
        config: LLMConfig,
        latency_ms: int,
        usage: Mapping[str, int],
    ) -> None:
        """Emit a payload-free success event for a typed Gateway stream."""
        attributes: dict[str, str | int | bool] = {
            "request_id": request_id,
            "purpose": request.purpose,
            "model": config.model,
            "provider": config.provider_name,
            "route": config.binding,
            "attempt": 1,
            "duration_ms": latency_ms,
            "outcome": "succeeded",
            "timed_out": False,
            "degraded": False,
        }
        token_total = _usage_total(usage)
        if token_total is not None:
            attributes["total_tokens"] = token_total
        cost_picousd = self._priced_cost_picousd(config.model, usage)
        if cost_picousd is not None:
            attributes["cost_picousd"] = cost_picousd
        record_product_event(
            self._event_sink,
            "gateway.complete",
            attributes,
        )

    def _priced_cost_picousd(self, model: str, usage: Mapping[str, int]) -> int | None:
        """Return optional server-priced usage without risking product work."""
        try:
            return self._token_pricing.cost_picousd(model, usage)
        except Exception:  # noqa: BLE001 - optional telemetry must fail open
            logger.warning("gateway_token_pricing_failed")
            return None

    def _record_stream_failure(
        self,
        request_id: str,
        request: GatewayRequest,
        prepared: PreparedGatewayCompletion,
        error: Exception,
        latency_ms: int,
    ) -> None:
        """Emit a payload-free timeout/error event without exposing stream data."""
        timed_out = isinstance(error, TimeoutError) or "timeout" in type(error).__name__.lower()
        config = prepared.config
        record_product_event(
            self._event_sink,
            "gateway.complete",
            {
                "request_id": request_id,
                "purpose": request.purpose,
                "model": config.model,
                "provider": config.provider_name,
                "route": config.binding,
                "attempt": 1,
                "duration_ms": latency_ms,
                "outcome": "timeout" if timed_out else "error",
                "timed_out": timed_out,
                "degraded": False,
            },
        )

    def _record_stream_cancelled(
        self,
        request_id: str,
        request: GatewayRequest,
        config: LLMConfig,
        latency_ms: int,
    ) -> None:
        """Record cancellation without treating it as a provider failure."""
        record_product_event(
            self._event_sink,
            "gateway.complete",
            {
                "request_id": request_id,
                "purpose": request.purpose,
                "model": config.model,
                "provider": config.provider_name,
                "route": config.binding,
                "attempt": 1,
                "duration_ms": latency_ms,
                "outcome": "cancelled",
                "timed_out": False,
                "degraded": False,
            },
        )

    @staticmethod
    def _provider_messages(request: GatewayRequest) -> list[dict[str, Any]]:
        """Build provider messages from the canonical typed request."""
        messages = request.messages or (
            GatewayMessage(role="system", content=request.system_prompt),
            GatewayMessage(role="user", content=request.prompt),
        )
        TraitTutorGateway._validate_tool_result_bindings(messages)
        return [message.to_provider() for message in messages]

    @staticmethod
    def _validate_tool_result_bindings(messages: Sequence[GatewayMessage]) -> None:
        """Require every replayed tool result to match an earlier assistant call.

        The gateway does not execute tools, but it must not send an orphan or
        substituted result into the next provider turn.  Call IDs are unique
        inside one request conversation, and an optional tool-result name is
        checked when a caller retains it for provider compatibility.
        """
        known_calls: dict[str, str] = {}
        resolved_calls: set[str] = set()
        for message in messages:
            if message.role == "assistant":
                for tool_call in message.tool_calls:
                    if tool_call.id in known_calls:
                        raise ValueError(
                            "Gateway assistant tool_call ids must be unique within a conversation"
                        )
                    known_calls[tool_call.id] = tool_call.name
                continue
            if message.role != "tool":
                continue
            tool_call_id = message.tool_call_id
            # GatewayMessage validates the non-empty string case, but keep this
            # guard local so the invariant remains obvious at this boundary.
            if tool_call_id is None or tool_call_id not in known_calls:
                raise ValueError(
                    "Gateway tool messages must reference an earlier assistant tool_call"
                )
            if tool_call_id in resolved_calls:
                raise ValueError("Gateway tool_call results may be supplied only once")
            expected_name = known_calls[tool_call_id]
            if message.name is not None and message.name != expected_name:
                raise ValueError("Gateway tool message name must match its assistant tool_call")
            resolved_calls.add(tool_call_id)

    @staticmethod
    def _normalized_attachments(
        attachments: Sequence[GatewayAttachment],
    ) -> tuple[GatewayAttachment, ...]:
        """Copy typed attachment values for provider preparation."""
        return tuple(attachments)

    @staticmethod
    def _normalized_tools(tools: Sequence[GatewayTool]) -> tuple[GatewayTool, ...]:
        """Copy typed tool schemas; tool-result loops remain caller-owned."""
        return tuple(tools)

    @staticmethod
    def _normalized_response_format(
        response_format: Mapping[str, Any] | None,
    ) -> dict[str, Any] | None:
        """Copy and validate the portable response-format envelope.

        Validation happens before capability fallback so invalid caller data is
        not silently hidden as a provider incompatibility.
        """
        if response_format is None:
            return None
        if not isinstance(response_format, Mapping):
            raise ValueError("Gateway response_format must be a mapping")
        normalized = dict(response_format)
        response_type = normalized.get("type")
        if response_type not in {"text", "json_object", "json_schema"}:
            raise ValueError("Gateway response_format needs a supported string 'type'")
        if response_type == "json_schema" and not isinstance(
            normalized.get("json_schema"), Mapping
        ):
            raise ValueError("Gateway json_schema response_format requires a json_schema mapping")
        return normalized


_gateway: TraitTutorGateway | None = None


def get_gateway() -> TraitTutorGateway:
    global _gateway
    if _gateway is None:
        _gateway = TraitTutorGateway()
    return _gateway
