"""Learner-safe contracts for canonical memory management.

The router layer is intentionally outside this module.  These contracts do not
accept an owner identifier: callers construct :class:`MemoryManagementService`
from the authenticated identity and cannot widen access through request data.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from traittutor.context_assembler.access import MemoryAccessRecord

from .models import MemoryCandidate, MemoryConflict, MemoryScope, UserMemoryItem


class CandidateActivationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: str = Field(min_length=1, max_length=128)
    confirmed: bool = False
    evidence_count: int = Field(default=0, ge=0, le=1000)


class CandidateRejectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: str = Field(min_length=1, max_length=128)


class MemoryMutationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: str = Field(min_length=1, max_length=128)


class MemoryGrant(BaseModel):
    """A durable, owner-bound authorization for one retrieval boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    grant_id: str = Field(min_length=1, max_length=96)
    owner_id: str = Field(min_length=1, max_length=128)
    requesting_scope: MemoryScope
    requesting_scope_id: str | None = Field(default=None, max_length=128)
    requesting_subject_id: str | None = Field(default=None, max_length=128)
    requesting_kc_id: str | None = Field(default=None, max_length=128)
    target_scope: MemoryScope
    target_scope_id: str | None = Field(default=None, max_length=128)
    target_subject_id: str | None = Field(default=None, max_length=128)
    target_kc_id: str | None = Field(default=None, max_length=128)
    purpose: str = Field(min_length=1, max_length=300)
    status: Literal["active", "revoked"] = "active"
    created_at: str
    # Authorization expiry is persisted by MemoryStore but omitted from the
    # existing learner-safe public grant projection until that API is versioned.
    expires_at: str | None = Field(default=None, exclude=True)
    revoked_at: str | None = None

    @field_validator("created_at", "expires_at", "revoked_at")
    @classmethod
    def _valid_timestamp(cls, value: str | None) -> str | None:
        if value is None:
            return value
        from .models import _require_utc_iso

        return _require_utc_iso(value)


class CreateMemoryGrantRequest(BaseModel):
    """Grant input deliberately omits ``owner_id`` and status fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: str = Field(min_length=1, max_length=128)
    requesting_scope: MemoryScope
    requesting_scope_id: str | None = Field(default=None, max_length=128)
    requesting_subject_id: str | None = Field(default=None, max_length=128)
    requesting_kc_id: str | None = Field(default=None, max_length=128)
    target_scope: MemoryScope
    target_scope_id: str | None = Field(default=None, max_length=128)
    target_subject_id: str | None = Field(default=None, max_length=128)
    target_kc_id: str | None = Field(default=None, max_length=128)
    purpose: str = Field(min_length=1, max_length=300)
    expires_at: str | None = None

    @field_validator("expires_at")
    @classmethod
    def _valid_expiry(cls, value: str | None) -> str | None:
        if value is None:
            return value
        from .models import _require_utc_iso

        return _require_utc_iso(value)


class MemorySearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: MemoryScope | None = None
    scope_id: str | None = Field(default=None, max_length=128)
    subject_id: str | None = Field(default=None, max_length=128)
    kc_id: str | None = Field(default=None, max_length=128)
    keyword: str | None = Field(default=None, max_length=500)
    requesting_scope: MemoryScope | None = None
    requesting_scope_id: str | None = Field(default=None, max_length=128)
    requesting_subject_id: str | None = Field(default=None, max_length=128)
    requesting_kc_id: str | None = Field(default=None, max_length=128)
    grant_id: str | None = Field(default=None, max_length=96)
    snapshot_id: str | None = Field(default=None, max_length=128)
    purpose: str = Field(default="memory_management", min_length=1, max_length=300)


class MemoryManagementSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidates: tuple[MemoryCandidate, ...]
    items: tuple[UserMemoryItem, ...]
    conflicts: tuple[MemoryConflict, ...]
    grants: tuple[MemoryGrant, ...]
    access_records: tuple[MemoryAccessRecord, ...]


class MemoryDeleteResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    item: UserMemoryItem
    invalidated_index_generation: int


__all__ = [
    "CandidateActivationRequest",
    "CandidateRejectionRequest",
    "CreateMemoryGrantRequest",
    "MemoryGrant",
    "MemoryDeleteResult",
    "MemoryManagementSnapshot",
    "MemoryMutationRequest",
    "MemorySearchRequest",
]
