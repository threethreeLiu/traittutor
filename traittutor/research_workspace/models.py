"""Frozen, owner-bound contracts for durable research workspaces.

The records are intentionally separate from the chat research pipeline.  They
describe product state only; provider prompts, chain-of-thought and credentials
are never accepted here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator, model_validator

ResearchWorkspaceStatus = Literal["active", "archived", "deleted"]
ResearchRunStatus = Literal[
    "draft",
    "queued",
    "running",
    "pausing",
    "paused",
    "cancelling",
    "cancelled",
    "completed",
    "failed",
    "needs_review",
]
ResearchClaimKind = Literal["grounded", "inference"]
ResearchReceiptOutcome = Literal["accepted", "discarded_stale", "failed"]
ResearchEvidenceStatus = Literal["active", "needs_review"]
ResearchSourceStatus = Literal["active", "invalidated"]


class ResearchKnowledgeBaseBinding(BaseModel):
    """Frozen logical reference to a KB authorized when a brief was saved.

    The binding deliberately stores only a logical resource identifier and a
    display name.  A filesystem root, credential, or provider implementation
    is resolved again in the worker's authenticated owner context immediately
    before retrieval; none of those implementation details belongs in durable
    research input or a public DTO.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    resource_id: str = Field(
        min_length=9,
        max_length=384,
        pattern=r"^(?:admin|user):kb:[^/\\\x00]+$",
    )
    display_name: str = Field(min_length=1, max_length=240)
    source: Literal["admin", "user"]
    authorized_owner_id: str = Field(min_length=1, max_length=128)

    @field_validator("display_name")
    @classmethod
    def _reject_path_like_display_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or "/" in normalized or "\\" in normalized:
            raise ValueError("knowledge base display_name must be a logical name")
        return normalized


def require_utc_iso(value: str) -> str:
    """Require an aware UTC timestamp for deterministic ordering."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("timestamp must be valid ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ValueError("timestamp must include a UTC offset")
    return value


class ResearchWorkspace(BaseModel):
    """A product workspace, visible only to its owning identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    workspace_id: str = Field(min_length=1, max_length=96)
    owner_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=240)
    subject_id: str | None = Field(default=None, max_length=128)
    status: ResearchWorkspaceStatus = "active"
    revision: int = Field(default=1, ge=1)
    active_brief_id: str | None = Field(default=None, max_length=96)
    created_at: str
    updated_at: str

    _validate_created_at = field_validator("created_at")(require_utc_iso)
    _validate_updated_at = field_validator("updated_at")(require_utc_iso)


class ResearchContinuationRef(BaseModel):
    """Immutable lineage from a follow-up brief to one validated report revision."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    parent_run_id: str = Field(min_length=1, max_length=96)
    report_id: str = Field(min_length=1, max_length=96)
    report_revision: int = Field(ge=1)


class ResearchBrief(BaseModel):
    """An immutable research input snapshot referenced by a run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    brief_id: str = Field(min_length=1, max_length=96)
    workspace_id: str = Field(min_length=1, max_length=96)
    owner_id: str = Field(min_length=1, max_length=128)
    version: int = Field(ge=1)
    question: str = Field(min_length=1, max_length=12_000)
    objectives: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    constraints: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    source_policy: Literal["web", "knowledge_base", "mixed"] = "web"
    knowledge_base: ResearchKnowledgeBaseBinding | None = None
    continuation: ResearchContinuationRef | None = None
    content_hash: str = Field(min_length=16, max_length=128)
    created_at: str

    _validate_created_at = field_validator("created_at")(require_utc_iso)

    @model_validator(mode="after")
    def _validate_knowledge_base_binding(self) -> "ResearchBrief":
        if self.source_policy in {"knowledge_base", "mixed"} and self.knowledge_base is None:
            raise ValueError("knowledge_base and mixed briefs require an authorized knowledge base")
        if self.source_policy == "web" and self.knowledge_base is not None:
            raise ValueError("web briefs cannot carry a knowledge base binding")
        if (
            self.knowledge_base is not None
            and self.knowledge_base.authorized_owner_id != self.owner_id
        ):
            raise ValueError("knowledge base binding is outside the brief owner partition")
        return self


class ResearchSource(BaseModel):
    """A clickable source captured in the workspace evidence ledger."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str = Field(min_length=1, max_length=96)
    workspace_id: str = Field(min_length=1, max_length=96)
    owner_id: str = Field(min_length=1, max_length=128)
    url: AnyHttpUrl
    title: str = Field(min_length=1, max_length=500)
    excerpt: str | None = Field(default=None, max_length=8_000)
    retrieved_at: str
    # Sources are never physically removed.  A later revision records an
    # invalidation so historic reports remain auditable while fresh reads do
    # not present the source as usable evidence.
    revision: int = Field(default=1, ge=1)
    status: ResearchSourceStatus = "active"
    invalidated_at: str | None = None
    invalidation_reason: str | None = Field(default=None, max_length=1_000)

    _validate_retrieved_at = field_validator("retrieved_at")(require_utc_iso)
    _validate_invalidated_at = field_validator("invalidated_at")(
        lambda value: require_utc_iso(value) if value is not None else value
    )

    @model_validator(mode="after")
    def _validate_invalidation(self) -> "ResearchSource":
        if self.status == "active" and (
            self.invalidated_at is not None or self.invalidation_reason is not None
        ):
            raise ValueError("active sources cannot carry invalidation metadata")
        if self.status == "invalidated" and self.invalidated_at is None:
            raise ValueError("invalidated sources require invalidated_at")
        return self


class ResearchNote(BaseModel):
    """A user-visible note that may cite stored source identifiers."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    note_id: str = Field(min_length=1, max_length=96)
    workspace_id: str = Field(min_length=1, max_length=96)
    owner_id: str = Field(min_length=1, max_length=128)
    body: str = Field(min_length=1, max_length=20_000)
    source_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=200)
    revision: int = Field(default=1, ge=1)
    created_at: str
    updated_at: str

    _validate_created_at = field_validator("created_at")(require_utc_iso)
    _validate_updated_at = field_validator("updated_at")(require_utc_iso)


class ResearchClaim(BaseModel):
    """A report claim that is grounded by source IDs or labelled inference."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_id: str = Field(min_length=1, max_length=96)
    workspace_id: str = Field(min_length=1, max_length=96)
    run_id: str = Field(min_length=1, max_length=96)
    owner_id: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=20_000)
    kind: ResearchClaimKind
    source_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=200)
    created_at: str
    revision: int = Field(default=1, ge=1)
    evidence_status: ResearchEvidenceStatus = "active"
    review_required_source_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=200)
    evidence_status_updated_at: str | None = None

    _validate_created_at = field_validator("created_at")(require_utc_iso)
    _validate_evidence_status_updated_at = field_validator("evidence_status_updated_at")(
        lambda value: require_utc_iso(value) if value is not None else value
    )

    @model_validator(mode="after")
    def _require_grounded_sources(self) -> "ResearchClaim":
        if self.kind == "grounded" and not self.source_ids:
            raise ValueError("grounded claims require one or more source IDs")
        if self.kind == "inference" and self.source_ids:
            raise ValueError("inference claims must not masquerade as sourced facts")
        if self.evidence_status == "active" and self.review_required_source_ids:
            raise ValueError("active claims cannot retain invalidated source markers")
        if self.evidence_status == "needs_review" and not self.review_required_source_ids:
            raise ValueError("review-required claims must identify invalidated sources")
        if not set(self.review_required_source_ids).issubset(self.source_ids):
            raise ValueError("review-required sources must belong to the claim")
        return self


class ResearchRun(BaseModel):
    """Durable execution state fenced from late or duplicate workers."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str = Field(min_length=1, max_length=96)
    workspace_id: str = Field(min_length=1, max_length=96)
    owner_id: str = Field(min_length=1, max_length=128)
    brief_id: str = Field(min_length=1, max_length=96)
    brief_version: int = Field(ge=1)
    input_hash: str = Field(min_length=16, max_length=128)
    idempotency_key: str = Field(min_length=1, max_length=160)
    status: ResearchRunStatus = "draft"
    revision: int = Field(default=1, ge=1)
    # Lease heartbeats are operational bookkeeping.  They must not invalidate
    # a user's state-transition CAS token, so they advance independently from
    # the public lifecycle revision.
    lease_revision: int = Field(default=1, ge=1)
    fencing_epoch: int = Field(default=1, ge=1)
    claim_token: str | None = Field(default=None, max_length=128)
    claimed_by: str | None = Field(default=None, max_length=128)
    lease_expires_at: str | None = None
    failure_reason: str | None = Field(default=None, max_length=1_000)
    created_at: str
    updated_at: str

    _validate_created_at = field_validator("created_at")(require_utc_iso)
    _validate_updated_at = field_validator("updated_at")(require_utc_iso)
    _validate_lease_expires_at = field_validator("lease_expires_at")(
        lambda value: require_utc_iso(value) if value is not None else value
    )


class ResearchTaskReceipt(BaseModel):
    """Idempotent durable result of one worker task, persisted before progress."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    receipt_id: str = Field(min_length=1, max_length=96)
    workspace_id: str = Field(min_length=1, max_length=96)
    run_id: str = Field(min_length=1, max_length=96)
    owner_id: str = Field(min_length=1, max_length=128)
    task_id: str = Field(min_length=1, max_length=160)
    input_hash: str = Field(min_length=16, max_length=128)
    fencing_epoch: int = Field(ge=1)
    outcome: ResearchReceiptOutcome
    detail: str | None = Field(default=None, max_length=4_000)
    created_at: str

    _validate_created_at = field_validator("created_at")(require_utc_iso)


class ResearchReportArtifact(BaseModel):
    """A versioned report projection over explicit claims and source references."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    report_id: str = Field(min_length=1, max_length=96)
    workspace_id: str = Field(min_length=1, max_length=96)
    run_id: str = Field(min_length=1, max_length=96)
    owner_id: str = Field(min_length=1, max_length=128)
    body: str = Field(min_length=1, max_length=100_000)
    claim_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=10_000)
    created_at: str
    revision: int = Field(default=1, ge=1)
    evidence_status: ResearchEvidenceStatus = "active"
    review_required_source_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=10_000)
    evidence_status_updated_at: str | None = None

    _validate_created_at = field_validator("created_at")(require_utc_iso)
    _validate_evidence_status_updated_at = field_validator("evidence_status_updated_at")(
        lambda value: require_utc_iso(value) if value is not None else value
    )

    @model_validator(mode="after")
    def _validate_evidence_status(self) -> "ResearchReportArtifact":
        if self.evidence_status == "active" and self.review_required_source_ids:
            raise ValueError("active reports cannot retain invalidated source markers")
        if self.evidence_status == "needs_review" and not self.review_required_source_ids:
            raise ValueError("review-required reports must identify invalidated sources")
        return self
