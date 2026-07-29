"""Turn-scoped chat-loop capabilities.

Each loop capability lives in its own subpackage under
:mod:`traittutor.capabilities` (``solve``, ``mastery``). The chat loop imports
only the generic registry/protocol from this package; feature-specific prompts,
tools, and kwargs injection stay inside each capability subpackage.

A loop capability is "chat engine + decoupled capability logic": it reuses the
full chat tool surface and adds its own owned tools + a system prompt block on
top when active, instead of running a bespoke pipeline.
"""

from traittutor.capabilities.protocol import KnowledgeCapability, LoopCapability, PromptBlock
from traittutor.capabilities.registry import (
    LOOP_CAPABILITIES,
    active_loop_capabilities,
    any_exclusive_capability_active,
    capability_tool_owners,
)

__all__ = [
    "LOOP_CAPABILITIES",
    "KnowledgeCapability",
    "LoopCapability",
    "PromptBlock",
    "active_loop_capabilities",
    "any_exclusive_capability_active",
    "capability_tool_owners",
]
