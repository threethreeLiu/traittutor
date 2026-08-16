"""
Stream Event Protocol
=====================

Defines the unified streaming event format used by all tools, capabilities,
and plugins to communicate progress and results to consumers (CLI, WebSocket, SDK).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import time
from typing import Any
from uuid import uuid4


class StreamEventType(str, Enum):
    """All possible event types in a streaming session."""

    STAGE_START = "stage_start"
    STAGE_END = "stage_end"
    THINKING = "thinking"
    OBSERVATION = "observation"
    CONTENT = "content"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    PROGRESS = "progress"
    SOURCES = "sources"
    RESULT = "result"
    ERROR = "error"
    SESSION = "session"
    SESSION_META = "session_meta"
    DONE = "done"
    WAIT_FOR_INPUT = "wait_for_input"


@dataclass
class StreamEvent:
    """
    A single streaming event emitted during a chat turn.

    Attributes:
        type: The semantic kind of this event.
        source: Which tool / capability / plugin produced it (e.g. "deep_solve").
        stage: Current stage within the source (e.g. "planning").
        content: Human-readable text payload.
        metadata: Arbitrary structured data (tool args, sources, metrics, …).
        timestamp: Unix epoch seconds when the event was created.
    """

    type: StreamEventType
    source: str = ""
    stage: str = ""
    content: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    session_id: str = ""
    turn_id: str = ""
    seq: int = 0
    timestamp: float = field(default_factory=time.time)
    event_id: str = field(default_factory=lambda: f"evt_{uuid4().hex}")
    request_id: str = ""

    @property
    def data(self) -> dict[str, Any]:
        """Return the single structured payload carried by this event."""

        return {
            "content": self.content,
            "stage": self.stage,
            "metadata": self.metadata,
        }

    def to_dict(self) -> dict[str, Any]:
        """Serialize the canonical event envelope.

        Payload fields live only under ``data``. Transport consumers must not
        depend on retired flattened payload keys.
        """

        return {
            "event_id": self.event_id,
            "request_id": self.request_id,
            "type": self.type.value,
            "data": self.data,
            "source": self.source,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "seq": self.seq,
            "timestamp": self.timestamp,
        }
