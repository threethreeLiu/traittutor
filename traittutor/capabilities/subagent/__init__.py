"""Subagent capability — consult a user's live local agent (Claude Code / Codex).

Selected like any connected KB: when the user picks a ``type: subagent`` KB, the
chat turn runs exclusively on the ``consult_subagent`` tool, driving the live CLI
through :mod:`traittutor.services.subagent` and streaming its native run to the
sidebar. The chat model gathers what it needs across a bounded number of
consults, then answers the user itself.
"""

from __future__ import annotations

from traittutor.capabilities.subagent.binding import connection_for_turn
from traittutor.capabilities.subagent.capability import SubagentCapability
from traittutor.capabilities.subagent.tools import (
    SUBAGENT_TOOL_NAMES,
    SUBAGENT_TOOL_TYPES,
    ConsultSubagentTool,
)

__all__ = [
    "SubagentCapability",
    "ConsultSubagentTool",
    "SUBAGENT_TOOL_NAMES",
    "SUBAGENT_TOOL_TYPES",
    "connection_for_turn",
]
