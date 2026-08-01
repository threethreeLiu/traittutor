"""Turn-scoped chat-loop capabilities.

Each loop capability lives in its own subpackage under
:mod:`traittutor.capabilities` (for example ``solve``). The chat loop imports
only the generic registry/protocol from this package; feature-specific prompts,
tools, and kwargs injection stay inside each capability subpackage.

A loop capability is "chat engine + decoupled capability logic": it reuses the
full chat tool surface and adds its own owned tools + a system prompt block on
top when active, instead of running a bespoke pipeline.
"""

from traittutor.capabilities.protocol import KnowledgeCapability, LoopCapability, PromptBlock


def __getattr__(name: str):
    """Lazily expose registry helpers without creating import cycles.

    The tool registry imports capability-owned tool definitions, and the
    capability registry imports the chat runtime. Importing registry helpers at
    package import time makes those two paths circular. Keep the public
    convenience names, but resolve them only when callers ask for them.
    """

    if name in {
        "LOOP_CAPABILITIES",
        "active_loop_capabilities",
        "any_exclusive_capability_active",
        "capability_tool_owners",
    }:
        from traittutor.capabilities import registry

        return getattr(registry, name)
    raise AttributeError(name)

__all__ = [
    "LOOP_CAPABILITIES",
    "KnowledgeCapability",
    "LoopCapability",
    "PromptBlock",
    "active_loop_capabilities",
    "any_exclusive_capability_active",
    "capability_tool_owners",
]
