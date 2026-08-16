"""Gateway-backed OpenAI-shaped adapter for shared agentic loops."""

from __future__ import annotations

import asyncio
import json
import re
from types import SimpleNamespace
from typing import Any

from traittutor.gateway import GatewayMessage, GatewayRequest, GatewayTool, get_gateway

_THINK_OPEN_RE = re.compile(r"<\s*think(?:ing)?\b[^>]*>", re.IGNORECASE)
_THINK_CLOSE_RE = re.compile(r"<\s*/\s*think(?:ing)?\s*>", re.IGNORECASE)
_TAG_HOLDBACK_CHARS = 24


class _PrivateThinkFilter:
    """Discard inline provider reasoning before it reaches an agentic loop."""

    def __init__(self) -> None:
        self._buffer = ""
        self._in_think = False

    def feed(self, chunk: str) -> list[str]:
        self._buffer += chunk
        visible: list[str] = []
        while True:
            pattern = _THINK_CLOSE_RE if self._in_think else _THINK_OPEN_RE
            match = pattern.search(self._buffer)
            if match is None:
                break
            if not self._in_think and match.start() > 0:
                visible.append(self._buffer[: match.start()])
            self._buffer = self._buffer[match.end() :]
            self._in_think = not self._in_think

        emit_upto = len(self._buffer)
        tag_start = self._buffer.rfind("<")
        if (
            tag_start != -1
            and len(self._buffer) - tag_start <= _TAG_HOLDBACK_CHARS
            and ">" not in self._buffer[tag_start:]
        ):
            emit_upto = tag_start
        if emit_upto:
            if not self._in_think:
                visible.append(self._buffer[:emit_upto])
            self._buffer = self._buffer[emit_upto:]
        return visible

    def flush(self) -> list[str]:
        if not self._buffer:
            return []
        buffered = self._buffer
        self._buffer = ""
        return [] if self._in_think else [buffered]


class GatewayAgenticClient:
    """Server-scoped Gateway adapter for one legacy-shaped agentic loop.

    The shared labeled-step engine still consumes the narrow
    ``client.chat.completions.create`` shape. This adapter converts that
    internal representation to typed Gateway messages and tools before every
    provider request. It is not a provider client and cannot bypass Gateway
    retry, fallback, telemetry, or cancellation policy.
    """

    def __init__(
        self,
        *,
        owner_id: str,
        cancellation_event: asyncio.Event,
        llm_config: Any,
        reasoning_effort: str | None,
        purpose_prefix: str,
        surface_name: str,
        timeout_seconds: float = 180.0,
    ) -> None:
        normalized_prefix = purpose_prefix.strip().strip(":")
        if not normalized_prefix:
            raise ValueError("Gateway agentic purpose prefix is required.")
        self._owner_id = owner_id
        self._cancellation_event = cancellation_event
        self._llm_config = llm_config
        self._reasoning_effort = reasoning_effort
        self._purpose_prefix = normalized_prefix
        self._surface_name = surface_name.strip() or "Agentic loop"
        self._timeout_seconds = timeout_seconds
        self.chat = SimpleNamespace(completions=_GatewayAgenticCompletions(self))

    async def _create(self, **kwargs: Any) -> Any:
        if not kwargs.get("stream"):
            return await self._complete_once(**kwargs)
        return self._stream_once(**kwargs)

    def _request(self, *, kwargs: dict[str, Any], purpose: str) -> GatewayRequest:
        raw_messages = kwargs.get("messages")
        if not isinstance(raw_messages, list):
            raise RuntimeError(f"Gateway {self._surface_name} requires a message list.")
        raw_tools = kwargs.get("tools") or []
        if not isinstance(raw_tools, list):
            raise RuntimeError(f"Gateway {self._surface_name} tools are not representable.")
        try:
            messages = tuple(GatewayMessage.from_mapping(message) for message in raw_messages)
            tools = tuple(GatewayTool.from_mapping(schema) for schema in raw_tools)
        except (AttributeError, TypeError, ValueError) as exc:
            raise RuntimeError(
                f"Gateway {self._surface_name} request is not representable."
            ) from exc
        max_tokens = _max_tokens(kwargs)
        temperature = kwargs.get("temperature")
        if not isinstance(temperature, (int, float)) or isinstance(temperature, bool):
            temperature = None
        return GatewayRequest(
            prompt="",
            system_prompt="",
            purpose=purpose,
            user_id=self._owner_id,
            messages=messages,
            attachments=(),
            tools=tools,
            reasoning_effort=self._reasoning_effort,
            temperature=float(temperature) if temperature is not None else None,
            max_tokens=max_tokens,
            timeout_seconds=self._timeout_seconds,
            llm_config=self._llm_config,
            cancellation_event=self._cancellation_event,
        )

    async def _complete_once(self, **kwargs: Any) -> Any:
        request = self._request(
            kwargs=kwargs,
            purpose=f"{self._purpose_prefix}:note",
        )
        text_parts: list[str] = []
        saw_terminal = False
        think_filter = _PrivateThinkFilter()
        async for event in get_gateway().stream(request):
            if event.type == "text" and event.text:
                text_parts.extend(think_filter.feed(event.text))
            elif event.type == "tool_call":
                raise RuntimeError(
                    f"Gateway {self._surface_name} emitted an unsupported typed tool call."
                )
            elif event.type == "final":
                saw_terminal = True
            elif event.type == "cancelled":
                raise asyncio.CancelledError
        text_parts.extend(think_filter.flush())
        if not saw_terminal:
            raise RuntimeError(
                f"Gateway {self._surface_name} stream ended without a terminal receipt."
            )
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="".join(text_parts)))]
        )

    async def _stream_once(self, **kwargs: Any):
        request = self._request(
            kwargs=kwargs,
            purpose=f"{self._purpose_prefix}:agentic_loop",
        )
        saw_terminal = False
        tool_index = 0
        allowed_tool_names = {tool.name for tool in request.tools}
        seen_tool_call_ids: set[str] = set()
        think_filter = _PrivateThinkFilter()
        async for event in get_gateway().stream(request):
            if event.type == "text" and event.text:
                for visible in think_filter.feed(event.text):
                    if visible:
                        yield _chunk(content=visible)
            elif event.type == "tool_call" and event.tool_call is not None:
                tool_call = event.tool_call
                if tool_call.name not in allowed_tool_names or tool_call.id in seen_tool_call_ids:
                    raise RuntimeError(
                        f"Gateway {self._surface_name} emitted an unsupported typed tool call."
                    )
                seen_tool_call_ids.add(tool_call.id)
                yield _chunk(
                    tool_call=SimpleNamespace(
                        index=tool_index,
                        id=tool_call.id,
                        function=SimpleNamespace(
                            name=tool_call.name,
                            arguments=json.dumps(
                                dict(tool_call.arguments),
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                        ),
                    )
                )
                tool_index += 1
            elif event.type == "usage" and event.usage is not None:
                yield _chunk(usage=SimpleNamespace(**dict(event.usage)))
            elif event.type == "final":
                for visible in think_filter.flush():
                    if visible:
                        yield _chunk(content=visible)
                saw_terminal = True
                yield _chunk(finish_reason=event.finish_reason or "stop")
                return
            elif event.type == "cancelled":
                raise asyncio.CancelledError

        for visible in think_filter.flush():
            if visible:
                yield _chunk(content=visible)
        if not saw_terminal:
            raise RuntimeError(
                f"Gateway {self._surface_name} stream ended without a terminal receipt."
            )


class _GatewayAgenticCompletions:
    def __init__(self, adapter: GatewayAgenticClient) -> None:
        self._adapter = adapter

    async def create(self, **kwargs: Any) -> Any:
        return await self._adapter._create(**kwargs)


def _max_tokens(kwargs: dict[str, Any]) -> int | None:
    """Read either OpenAI token-limit spelling without accepting booleans."""
    for key in ("max_tokens", "max_completion_tokens"):
        value = kwargs.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
    return None


def _chunk(
    *,
    content: str | None = None,
    tool_call: Any = None,
    usage: Any = None,
    finish_reason: str | None = None,
) -> SimpleNamespace:
    """Build the narrow OpenAI-like stream frame consumed by labeled steps."""
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(
                    content=content,
                    tool_calls=[tool_call] if tool_call else None,
                ),
                finish_reason=finish_reason,
            )
        ],
        usage=usage,
    )


__all__ = ["GatewayAgenticClient"]
