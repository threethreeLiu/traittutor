"""Immutable context references for deterministic assistant execution.

Snapshots contain only bounded, versioned references.  They intentionally do
not copy full conversations, learner profiles, raw personality scales, memory
documents, or concept mastery payloads.  This keeps downstream agents on the
minimum context required for one task and prevents a frozen prompt boundary
from becoming a second mutable source of learner truth.
"""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from traittutor.research_workspace.provenance import ResearchCoursewareProvenance

AssistantIntent = Literal["chat", "search", "research", "learn", "create"]


def _require_utc_iso(value: str) -> str:
    """Reject naive or non-UTC timestamps so audit records compare safely."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("timestamp must be valid ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ValueError("timestamp must include a UTC offset")
    return value


class MemoryRef(BaseModel):
    """A reference to one memory partition item, never its private content."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: str
    key: str
    version: str | None = None


class ConceptSignalRef(BaseModel):
    """A content-versioned pointer to the canonical personalization signal.

    The canonical ``ConceptSignal`` remains in ``personalization``.  Keeping
    only its identifier and version here prevents this module from cloning or
    independently updating BKT fields such as mastery, guess, slip, or
    transition probabilities.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    concept_id: str
    version: str


class TutorPersonaRef(BaseModel):
    """A non-private provenance pointer for the applied expression contract.

    The complete persona profile stays in its owner-bound store. A context
    snapshot may prove which compiled, allowlisted expression contract was
    available to a turn, but must not duplicate presentation settings or any
    free-form legacy persona text.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    profile_ref: str
    contract_hash: str


class SubjectLearningStateRef(BaseModel):
    """Identity-bound provenance for a canonical subject-state read.

    The referenced governance snapshot remains a read-only reconstruction from
    immutable events. This reference carries no answer, rubric, raw BKT
    posterior, or mutable review schedule.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    owner_id: str
    subject_id: str
    source_revision: str
    param_version: str
    strong_event_count: int = Field(ge=0)
    calibrated: bool


class SnapshotReadRanges(BaseModel):
    """The exact versioned ranges consulted while assembling one turn.

    Conversation and research objects are intentionally references because
    those v2.7 subsystems may evolve independently.  The snapshot records what
    was read without taking ownership of their lifecycle or rewriting an
    already published page when newer state appears.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    thread_version: str | None = None
    active_branch_version: str | None = None
    episode_ids: list[str] = Field(default_factory=list)
    memory_refs: list[MemoryRef] = Field(default_factory=list)
    research_run_id: str | None = None
    # Stable evidence identity only; this snapshot never carries report text,
    # claim content, or source/browser URLs.
    research_provenance: ResearchCoursewareProvenance | None = None
    learner_profile_version: str | None = None
    concept_signal_refs: list[ConceptSignalRef] = Field(default_factory=list)
    tutor_persona_ref: TutorPersonaRef | None = None
    subject_learning_state_ref: SubjectLearningStateRef | None = None


class AssistantContextSnapshot(BaseModel):
    """Frozen context boundary for exactly one assistant turn.

    Immutability is a product invariant: later memory consolidation, learner
    events, or BKT updates must affect a new snapshot rather than silently
    changing the prompt behind an already displayed response or page.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    snapshot_id: str = ""
    trace_id: str
    created_at: str
    intent: AssistantIntent
    user_id: str
    subject_id: str | None = None
    thread_id: str | None = None
    prompt_bundle_id: str | None = None
    token_budget: int = Field(ge=0)
    token_used: int = Field(default=0, ge=0)
    trim_reason: str | None = None
    read_ranges: SnapshotReadRanges = Field(default_factory=SnapshotReadRanges)
    degraded: bool = False
    degradation_reason: str | None = None

    _validate_created_at = field_validator("created_at")(_require_utc_iso)

    def model_post_init(self, __context: object) -> None:
        """Derive an id when callers do not already own an idempotency key.

        ``object.__setattr__`` is deliberately limited to construction time;
        after validation the Pydantic model remains frozen.  The seed hash sees
        the caller-provided empty id, so identical inputs derive the same id.
        """
        del __context
        if not self.snapshot_id:
            object.__setattr__(self, "snapshot_id", f"ctx_{self.content_hash()[:16]}")

    def content_hash(self) -> str:
        """Return a stable SHA-256 digest of the complete JSON snapshot.

        Explicit JSON mode, retained ``None`` fields, sorted keys, and compact
        separators remove serialization-order and whitespace differences.  A
        caller that supplies the same timestamp and versioned inputs therefore
        receives the same digest on every process.
        """
        canonical = json.dumps(
            self.model_dump(mode="json", exclude_none=False),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class LearningContextSnapshot(AssistantContextSnapshot):
    """A minimal learning-only extension of the assistant context boundary.

    Plan and page fields remain references.  In particular, this model never
    carries answer keys and never derives mastery; grading and canonical BKT
    updates stay in their existing server-owned services.
    """

    intent: Literal["learn"] = "learn"
    teaching_plan_ref: str | None = None
    component_plan_ref: str | None = None
    surface_type: str | None = None
    page_id: str | None = None
