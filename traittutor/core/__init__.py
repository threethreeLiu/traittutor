"""Core contracts shared across runtime, tools, and capabilities."""

from .capability_protocol import BaseCapability, CapabilityManifest
from .context import Attachment, UnifiedContext
from .stream import StreamEvent, StreamEventType
from .stream_bus import StreamBus
from .tool_protocol import (
    BaseTool,
    ToolAlias,
    ToolDefinition,
    ToolParameter,
    ToolPromptHints,
    ToolResult,
)
from .trace import build_trace_metadata, merge_trace_metadata, new_call_id

__all__ = [
    "StreamEvent",
    "StreamEventType",
    "StreamBus",
    "new_call_id",
    "build_trace_metadata",
    "merge_trace_metadata",
    "BaseTool",
    "ToolAlias",
    "ToolDefinition",
    "ToolParameter",
    "ToolPromptHints",
    "ToolResult",
    "BaseCapability",
    "CapabilityManifest",
    "UnifiedContext",
    "Attachment",
]
