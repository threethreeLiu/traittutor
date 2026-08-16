"""Closed transition table for durable research run lifecycle."""

from __future__ import annotations

from .models import ResearchRunStatus

_ALLOWED_TRANSITIONS: dict[ResearchRunStatus, frozenset[ResearchRunStatus]] = {
    "draft": frozenset({"queued", "cancelled"}),
    "queued": frozenset({"running", "paused", "cancelling", "cancelled", "failed"}),
    "running": frozenset({"pausing", "cancelling", "completed", "failed", "needs_review"}),
    "pausing": frozenset({"paused", "cancelling", "failed"}),
    "paused": frozenset({"queued", "cancelling", "cancelled"}),
    "cancelling": frozenset({"cancelled", "failed"}),
    "cancelled": frozenset(),
    "completed": frozenset(),
    "failed": frozenset({"queued", "cancelled"}),
    "needs_review": frozenset({"queued", "cancelled"}),
}

TERMINAL_RUN_STATUSES: frozenset[ResearchRunStatus] = frozenset({"cancelled", "completed"})


class ResearchRunTransitionError(ValueError):
    """Raised when a lifecycle mutation would silently violate the run contract."""


def can_transition(current: ResearchRunStatus, target: ResearchRunStatus) -> bool:
    """Return whether one explicit lifecycle transition is legal."""
    return target in _ALLOWED_TRANSITIONS[current]


def require_transition(current: ResearchRunStatus, target: ResearchRunStatus) -> None:
    """Reject an illegal transition rather than coercing a state change."""
    if not can_transition(current, target):
        raise ResearchRunTransitionError(
            f"cannot transition research run {current!r} -> {target!r}"
        )
