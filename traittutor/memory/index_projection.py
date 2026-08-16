"""Typed, traceable long-term memory indexes built from canonical memory."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
import json
from typing import Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import UserMemoryItem, _require_utc_iso

AssertionState = Literal["verified", "inferred_confirmed"]
EvidenceType = Literal["explicit_statement", "confirmed_inference"]


def _now() -> str:
    return datetime.now(UTC).isoformat()


class MemoryIndexClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_id: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=4000)
    source_entry_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    source_refs: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    observed_from: str | None = None
    observed_until: str | None = None
    scope: str
    scope_id: str | None = Field(default=None, max_length=128)
    subject_id: str | None = Field(default=None, max_length=128)
    kc_id: str | None = Field(default=None, max_length=128)
    evidence_type: EvidenceType
    confidence: float | None = Field(default=None, ge=0, le=1)
    assertion_state: AssertionState

    @model_validator(mode="after")
    def _provenance_is_explicit(self) -> MemoryIndexClaim:
        if not self.source_entry_ids:
            raise ValueError("verified claims require canonical source_entry_ids")
        return self


class MemoryIndex(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    index_id: str = Field(min_length=1, max_length=128)
    owner_id: str = Field(min_length=1, max_length=128)
    entry_id: str = Field(min_length=1, max_length=128)
    generation: int = Field(ge=0)
    index_version: int = Field(ge=1)
    markdown: str = Field(max_length=100_000)
    claims: tuple[MemoryIndexClaim, ...] = Field(max_length=1000)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: str
    updated_at: str

    _created_at = field_validator("created_at")(_require_utc_iso)
    _updated_at = field_validator("updated_at")(_require_utc_iso)


def _content_hash(markdown: str, claims: tuple[MemoryIndexClaim, ...]) -> str:
    canonical = json.dumps(
        {"markdown": markdown, "claims": [claim.model_dump(mode="json") for claim in claims]},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def build_memory_index(
    *,
    owner_id: str,
    entry_id: str,
    generation: int,
    items: Iterable[UserMemoryItem],
    index_version: int = 1,
    now: str | None = None,
) -> MemoryIndex:
    """Build a deterministic display index from active canonical facts."""
    ordered = sorted(items, key=lambda item: (item.key, item.memory_id))
    claims: list[MemoryIndexClaim] = []
    for item in ordered:
        if item.owner_id != owner_id:
            raise ValueError("index source belongs to another owner")
        if item.status != "active":
            raise ValueError("only active canonical memories are index-eligible")
        refs = tuple(
            dict.fromkeys((*item.evidence_refs, *((item.source_ref,) if item.source_ref else ())))
        )
        claims.append(
            MemoryIndexClaim(
                claim_id=f"claim:{item.memory_id}",
                text=f"{item.key}: {item.value}",
                source_entry_ids=(item.memory_id,),
                source_refs=refs,
                observed_from=item.valid_from,
                observed_until=item.valid_until,
                scope=item.scope,
                scope_id=item.scope_id,
                subject_id=item.subject_id,
                kc_id=item.kc_id,
                evidence_type=(
                    "explicit_statement" if item.provenance == "explicit" else "confirmed_inference"
                ),
                confidence=item.confidence,
                assertion_state=(
                    "verified" if item.provenance == "explicit" else "inferred_confirmed"
                ),
            )
        )
    frozen_claims = tuple(claims)
    markdown = "\n".join(f"- {claim.text}" for claim in frozen_claims)
    timestamp = now or _now()
    return MemoryIndex(
        index_id=f"memory-index:{entry_id}",
        owner_id=owner_id,
        entry_id=entry_id,
        generation=generation,
        index_version=index_version,
        markdown=markdown,
        claims=frozen_claims,
        content_hash=_content_hash(markdown, frozen_claims),
        created_at=timestamp,
        updated_at=timestamp,
    )


def validate_source_allowlist(index: MemoryIndex, allowed_memory_ids: set[str]) -> None:
    """Reject model-supplied or stale canonical identifiers fail-closed."""
    referenced = {source_id for claim in index.claims for source_id in claim.source_entry_ids}
    unknown = referenced - allowed_memory_ids
    if unknown:
        raise ValueError(f"index contains unknown source ids: {sorted(unknown)!r}")


__all__ = [
    "AssertionState",
    "EvidenceType",
    "MemoryIndexClaim",
    "MemoryIndex",
    "build_memory_index",
    "validate_source_allowlist",
]
