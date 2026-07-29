"""Trace helpers for rendering structured execution timelines in the UI."""

from __future__ import annotations

from typing import Any
from uuid import uuid4


def new_call_id(prefix: str = "call") -> str:
    """Generate a short stable-enough id for one visible trace card."""
    return f"{prefix}-{uuid4().hex[:10]}"


def build_trace_metadata(
    *,
    call_id: str,
    phase: str,
    label: str,
    call_kind: str,
    trace_id: str | None = None,
    trace_role: str | None = None,
    trace_group: str | None = None,
    trace_kind: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "call_id": call_id,
        "phase": phase,
        "label": label,
        "call_kind": call_kind,
    }
    if trace_id:
        metadata["trace_id"] = trace_id
    if trace_role:
        metadata["trace_role"] = trace_role
    if trace_group:
        metadata["trace_group"] = trace_group
    if trace_kind:
        metadata["trace_kind"] = trace_kind
    metadata.update({k: v for k, v in extra.items() if v is not None})
    return metadata


def derive_trace_metadata(
    base: dict[str, Any] | None = None,
    *,
    call_id: str | None = None,
    phase: str | None = None,
    label: str | None = None,
    call_kind: str | None = None,
    trace_id: str | None = None,
    trace_role: str | None = None,
    trace_group: str | None = None,
    trace_kind: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    metadata = dict(base or {})
    overrides = {
        "call_id": call_id,
        "phase": phase,
        "label": label,
        "call_kind": call_kind,
        "trace_id": trace_id,
        "trace_role": trace_role,
        "trace_group": trace_group,
        "trace_kind": trace_kind,
    }
    metadata.update({key: value for key, value in overrides.items() if value is not None})
    metadata.update({key: value for key, value in extra.items() if value is not None})
    return metadata


def merge_trace_metadata(
    base: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    if base:
        merged.update(base)
    if extra:
        merged.update(extra)
    return merged


__all__ = [
    "build_trace_metadata",
    "derive_trace_metadata",
    "merge_trace_metadata",
    "new_call_id",
]
