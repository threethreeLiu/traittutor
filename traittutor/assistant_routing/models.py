"""Frozen capability-routing contracts.

The routing record intentionally contains a digest, not the user's raw
message.  A route selects a capability; it is not a second conversation or
memory store.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Capability = Literal["chat", "search", "research", "learn", "create"]
DecisionStatus = Literal[
    "ready",
    "confirmation_required",
    "completed",
    "confirmed",
    "failed",
]
SearchReceiptStatus = Literal["ready", "unavailable"]
SearchDegradationCode = Literal[
    "search_unavailable",
    "no_citable_sources",
]


def utc_now() -> str:
    """Return the stable UTC representation used by durable route records."""
    return datetime.now(UTC).isoformat()


class SearchSourceRef(BaseModel):
    """One learner-safe source minted only from a search tool citation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str = Field(pattern=r"^web-[0-9a-f]{16}$")
    reference: str = Field(pattern=r"^\[S[1-9][0-9]*\]$")
    title: str = Field(min_length=1, max_length=240)
    url: str = Field(min_length=1, max_length=2_000)
    snippet: str = Field(default="", max_length=1_000)
    source_type: Literal["web"] = "web"

    @field_validator("url")
    @classmethod
    def require_public_web_url(cls, value: str) -> str:
        from urllib.parse import urlsplit

        normalized = value.strip()
        parsed = urlsplit(normalized)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or any(character.isspace() or ord(character) < 32 for character in normalized)
        ):
            raise ValueError("search source URL must use HTTP(S)")
        return normalized


class SearchReceipt(BaseModel):
    """Durable public receipt for one controlled Search execution and delivery."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["search-receipt.v1"] = "search-receipt.v1"
    status: SearchReceiptStatus
    content: str = Field(min_length=1, max_length=12_000)
    sources: tuple[SearchSourceRef, ...] = Field(default_factory=tuple, max_length=12)
    source_refs: tuple[str, ...] = Field(default_factory=tuple, max_length=12)
    server_authored: Literal[True] = True
    session_id: str | None = Field(default=None, max_length=128)
    user_message_id: int | None = Field(default=None, ge=1)
    message_id: int | None = Field(default=None, ge=1)
    degradation_code: SearchDegradationCode | None = None

    @model_validator(mode="after")
    def validate_source_contract(self) -> SearchReceipt:
        expected_refs = tuple(source.source_id for source in self.sources)
        if self.source_refs != expected_refs:
            raise ValueError("search source_refs must match the structured sources")
        if self.status == "ready" and (not self.sources or self.degradation_code is not None):
            raise ValueError("ready search receipts require sources and no degradation")
        if self.status == "unavailable" and (
            self.sources or self.source_refs or self.degradation_code is None
        ):
            raise ValueError("unavailable search receipts cannot claim sources")
        return self


class CapabilityDecision(BaseModel):
    """One owner-bound, replayable routing decision.

    ``action_target`` is deliberately a typed hand-off receipt.  It must not
    claim that research, pack creation, or generation already ran.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["capability-decision.v1"] = "capability-decision.v1"
    decision_id: str = Field(min_length=1, max_length=96)
    owner_id: str = Field(min_length=1, max_length=128)
    message_digest: str = Field(min_length=64, max_length=64)
    session_id: str | None = Field(default=None, max_length=128)
    capability: Capability
    requested_capability: Capability | None = None
    manual_override: bool = False
    status: DecisionStatus
    requires_confirmation: bool
    action_target: dict[str, object]
    reason: str = Field(min_length=1, max_length=240)
    fallback_from: Capability | None = None
    search_receipt: SearchReceipt | None = None
    # Confirmed inputs stay in their owner-bound destination. This digest pins
    # retries to the same accepted contract without exposing private text.
    confirmation_input_hash: str | None = Field(default=None, min_length=64, max_length=64)
    revision: int = Field(ge=1)
    created_at: str
    updated_at: str

    @field_validator("session_id")
    @classmethod
    def normalize_session_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


__all__ = [
    "Capability",
    "CapabilityDecision",
    "DecisionStatus",
    "SearchDegradationCode",
    "SearchReceipt",
    "SearchReceiptStatus",
    "SearchSourceRef",
    "utc_now",
]
