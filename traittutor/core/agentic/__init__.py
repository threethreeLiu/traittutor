r"""Foundational agentic engine primitives.

These modules implement the chat-style ``\`\`LABEL\`\`+content`` LLM protocol as
reusable building blocks. Any capability that wants a streaming, label-driven
LLM loop (chat, solve step, etc.) composes them.

Layering:

* :mod:`labels`         — protocol-label parsing (parametric label set).
* :mod:`client`         — provider-normalized Gateway completion kwargs.
* :mod:`usage`          — token-usage accumulator shared across steps.
* :mod:`labeled_step`   — one streaming LLM call with label routing.
* :mod:`tool_dispatch`  — parallel tool execution with per-tool sub-traces.
* :mod:`loop`           — iteration scheduler that ties the above together.

Capability-specific concerns (system prompt assembly, tool whitelist, KB enums,
answer-now fast paths, force-finalize strategies, context-window guards) live
in each capability's own module — the primitives expose hooks but do not bake
those decisions in.
"""

from traittutor.core.agentic.client import (
    build_completion_kwargs,
)
from traittutor.core.agentic.labeled_step import LabeledStepResult, run_labeled_step
from traittutor.core.agentic.labels import (
    LABEL_PROBE_MAX_CHARS,
    LABEL_UNKNOWN,
    classify_label,
    find_inline_labels,
    strip_label_probe_prefix,
)
from traittutor.core.agentic.loop import LabelProtocol, LoopHost, LoopOutcome, run_agentic_loop
from traittutor.core.agentic.tool_dispatch import (
    MAX_PARALLEL_TOOL_CALLS,
    DispatchOutcome,
    dispatch_tool_calls,
    execute_tool_call,
)
from traittutor.core.agentic.usage import UsageTracker

__all__ = [
    "LABEL_PROBE_MAX_CHARS",
    "LABEL_UNKNOWN",
    "LabelProtocol",
    "LabeledStepResult",
    "LoopHost",
    "LoopOutcome",
    "MAX_PARALLEL_TOOL_CALLS",
    "DispatchOutcome",
    "UsageTracker",
    "build_completion_kwargs",
    "classify_label",
    "dispatch_tool_calls",
    "execute_tool_call",
    "find_inline_labels",
    "run_agentic_loop",
    "run_labeled_step",
    "strip_label_probe_prefix",
]
