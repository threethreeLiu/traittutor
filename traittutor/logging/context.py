"""Request-scoped logging context."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import contextvars
from typing import Any

LOG_CONTEXT_FIELDS = (
    "request_id",
    "turn_id",
    "session_id",
    "task_id",
    "capability",
    "stage",
    "sink",
)

_context: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "traittutor_log_context", default={}
)


def current_log_context() -> dict[str, Any]:
    """Return a copy of the active logging context."""
    return dict(_context.get())


@contextmanager
def bind_log_context(**fields: Any) -> Iterator[dict[str, Any]]:
    """Temporarily bind structured fields to all log records in this context."""
    clean_fields = {key: value for key, value in fields.items() if value is not None}
    previous = _context.get()
    token = _context.set({**previous, **clean_fields})
    try:
        yield current_log_context()
    finally:
        _context.reset(token)
