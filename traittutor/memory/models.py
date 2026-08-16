"""Scoped canonical memory objects: candidates, items, and lifecycle.

These objects govern how inferred vs explicit facts become durable user memory
(invariant #7). Inferred facts are candidates only; they activate on repeated
evidence or explicit user confirmation, never by a silent model rewrite. An
item that supersedes another links via ``supersedes_id``; the old item is
marked superseded, not deleted, so history stays traceable. Reading, searching,
or bookmarking these objects NEVER updates BKT (invariant #2) — they are
factual memory and exposure signals, not mastery evidence.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

MemoryScope = Literal["conversation", "research", "project", "subject", "global"]
MemoryStatus = Literal["candidate", "active", "superseded", "dormant", "deleted"]
MemoryCandidateStatus = Literal["candidate", "conflict", "activated", "rejected", "deleted"]
MemorySensitivity = Literal["public", "personal", "sensitive"]
MemoryProvenance = Literal["explicit", "inferred"]
MemoryAction = Literal[
    "candidate",
    "conflict",
    "activate",
    "supersede",
    "deactivate",
    "delete",
    "reject",
]


def _require_utc_iso(value: str) -> str:
    """Require an aware UTC ISO timestamp for durable ordering."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("timestamp must be valid ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ValueError("timestamp must include a UTC offset")
    return value


class MemoryCandidate(BaseModel):
    """A proposed memory awaiting activation. Never silently durable on its own."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str = Field(min_length=1, max_length=96)
    owner_id: str = Field(min_length=1, max_length=128)
    scope: MemoryScope
    scope_id: str | None = Field(default=None, max_length=128)
    subject_id: str | None = Field(default=None, max_length=128)
    kc_id: str | None = Field(default=None, max_length=128)
    key: str = Field(min_length=1, max_length=160)
    value: str = Field(min_length=1, max_length=4000)
    provenance: MemoryProvenance
    status: MemoryCandidateStatus = "candidate"
    confidence: float = Field(ge=0, le=1)
    sensitivity: MemorySensitivity = "personal"
    evidence_refs: tuple[str, ...] = Field(default_factory=tuple, max_length=24)
    source_ref: str | None = None  # clickable source required for research claims
    proposed_supersedes_id: str | None = Field(default=None, max_length=96)
    conflict_memory_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    valid_from: str | None = None
    valid_until: str | None = None
    created_at: str

    _validate_valid_from = field_validator("valid_from")(
        lambda value: _require_utc_iso(value) if value is not None else value
    )
    _validate_valid_until = field_validator("valid_until")(
        lambda value: _require_utc_iso(value) if value is not None else value
    )
    _validate_created_at = field_validator("created_at")(_require_utc_iso)


class UserMemoryItem(BaseModel):
    """One durable, status-tracked user memory fact."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    memory_id: str = Field(min_length=1, max_length=96)
    owner_id: str = Field(min_length=1, max_length=128)
    scope: MemoryScope
    scope_id: str | None = Field(default=None, max_length=128)
    subject_id: str | None = Field(default=None, max_length=128)
    kc_id: str | None = Field(default=None, max_length=128)
    key: str = Field(min_length=1, max_length=160)
    value: str = Field(min_length=1, max_length=4000)
    provenance: MemoryProvenance
    status: MemoryStatus
    confidence: float = Field(ge=0, le=1)
    sensitivity: MemorySensitivity = "personal"
    valid_from: str
    valid_until: str | None = None
    supersedes_id: str | None = None
    evidence_refs: tuple[str, ...] = Field(default_factory=tuple, max_length=24)
    source_ref: str | None = None
    created_at: str
    updated_at: str

    _validate_valid_from = field_validator("valid_from")(_require_utc_iso)
    _validate_valid_until = field_validator("valid_until")(
        lambda value: _require_utc_iso(value) if value is not None else value
    )
    _validate_created_at = field_validator("created_at")(_require_utc_iso)
    _validate_updated_at = field_validator("updated_at")(_require_utc_iso)


class MemoryLifecycleRecord(BaseModel):
    """Append-only provenance for every durable memory lifecycle transition."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    record_id: str = Field(min_length=1, max_length=96)
    owner_id: str = Field(min_length=1, max_length=128)
    memory_id: str | None = Field(default=None, max_length=96)
    candidate_id: str | None = Field(default=None, max_length=96)
    action: MemoryAction
    resulting_status: MemoryStatus | MemoryCandidateStatus
    source: str = Field(min_length=1, max_length=500)
    source_ref: str | None = None
    created_at: str

    _validate_created_at = field_validator("created_at")(_require_utc_iso)


class MemoryConflict(BaseModel):
    """Display-safe collision between active facts in the same partition."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: MemoryScope
    scope_id: str | None = None
    subject_id: str | None = None
    kc_id: str | None = None
    key: str
    candidate_id: str
    candidate_value: str
    memory_ids: tuple[str, ...]
    values: tuple[str, ...]
