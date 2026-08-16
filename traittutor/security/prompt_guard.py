"""Fail-closed guard for untrusted text entering an agent prompt.

The browser selects a capability by typed configuration. It must never submit
private mode markers or prompt text as if they were a learner message. This
guard also reuses the deterministic injection detector for role overrides,
instruction bypasses, secret extraction, and tool escalation.
"""

from __future__ import annotations

from dataclasses import dataclass

from traittutor.learning.intent import scan_for_prompt_injection

_RESERVED_INTERNAL_MARKERS = (
    "[TRAITTUTOR_GUIDED_SOLVE_V1]",
    "[TRAITTUTOR_LEARNING_EXPLORATION_V1]",
    "[TRAITTUTOR_KNOWLEDGE_DIAGRAM_V1]",
    "[TRAITTUTOR_HUMANIZER]",
)


@dataclass(frozen=True, slots=True)
class PromptGuardDecision:
    allowed: bool
    category: str | None = None


class PromptGuardRejected(ValueError):
    """Raised before unsafe prompt-like input can be persisted or executed."""

    def __init__(self, category: str) -> None:
        super().__init__("Input was rejected by the prompt safety policy.")
        self.category = category


def inspect_prompt_input(content: str) -> PromptGuardDecision:
    """Classify raw user text without returning private prompt details."""

    normalized_upper = content.upper()
    if any(marker in normalized_upper for marker in _RESERVED_INTERNAL_MARKERS):
        return PromptGuardDecision(False, "reserved_internal_prompt_marker")
    action, category = scan_for_prompt_injection(content, max_length=max(len(content), 1))
    if action == "block":
        return PromptGuardDecision(False, category or "prompt_injection")
    return PromptGuardDecision(True)


def enforce_prompt_guard(content: str) -> None:
    """Reject unsafe user text at the current trust boundary."""

    decision = inspect_prompt_input(content)
    if not decision.allowed:
        raise PromptGuardRejected(decision.category or "prompt_injection")


__all__ = [
    "PromptGuardDecision",
    "PromptGuardRejected",
    "enforce_prompt_guard",
    "inspect_prompt_input",
]
