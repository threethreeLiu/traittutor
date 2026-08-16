"""Learner-safe public contracts for learning governance views.

These models deliberately do not have fields for answers, rubrics, grading
rules, or raw prompts.  Projection code must construct them explicitly rather
than dumping an internal LearningPack or learning-progress object.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from traittutor.learning.models import ErrorRecordStatus, ErrorType, KnowledgeType
from traittutor.learning_model.events import AmendmentAction, AmendmentReason
from traittutor.learning_model.misconception import MisconceptionStatus
from traittutor.learning_model.stage_policy import ChangeSignal, EvidenceState


class GovernanceAttributionStatus(str, Enum):
    """Whether subject/KC attribution is backed by canonical strong evidence."""

    VERIFIED = "verified"
    ATTRIBUTION_PENDING = "attribution_pending"


class ReviewStatus(str, Enum):
    DUE = "due"
    UPCOMING = "upcoming"
    NEEDS_REBUILD = "needs_rebuild"


class ErrorSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    error_id: str = Field(min_length=1, max_length=96)
    question_id: str = Field(min_length=1, max_length=160)
    subject_id: str = Field(min_length=1, max_length=96)
    kc_id: str = Field(min_length=1, max_length=96)
    module_id: str = Field(default="", max_length=96)
    error_type: ErrorType
    status: ErrorRecordStatus
    attribution_status: GovernanceAttributionStatus
    source_event_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=64)
    created_at: float
    repaired_at: float | None = None
    relapsed_at: float | None = None
    last_seen_at: float | None = None


class RepairSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    error_id: str = Field(min_length=1, max_length=96)
    subject_id: str = Field(min_length=1, max_length=96)
    kc_id: str = Field(min_length=1, max_length=96)
    status: ErrorRecordStatus
    attribution_status: GovernanceAttributionStatus
    attempt_count: int = Field(ge=0)
    successful_attempt_count: int = Field(ge=0)
    last_attempt_at: float | None = None


class MisconceptionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    hypothesis_id: str = Field(min_length=1, max_length=96)
    subject_id: str = Field(min_length=1, max_length=96)
    kc_ids: tuple[str, ...] = Field(min_length=1, max_length=24)
    pattern: str = Field(min_length=1, max_length=400)
    status: MisconceptionStatus
    attribution_status: GovernanceAttributionStatus
    evidence_count: int = Field(ge=0)
    created_at: str
    updated_at: str


class ReviewSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    review_id: str = Field(min_length=1, max_length=160)
    learning_path_id: str = Field(min_length=1, max_length=160)
    subject_id: str = Field(min_length=1, max_length=96)
    kc_id: str = Field(min_length=1, max_length=96)
    knowledge_type: KnowledgeType
    due_at: float
    priority: int = Field(ge=0)
    status: ReviewStatus
    attribution_status: GovernanceAttributionStatus
    interval_index: int = Field(ge=0)


class SubjectKnowledgeEvidence(BaseModel):
    """Learner-safe, canonical evidence summary for one KC.

    This is a read model over the immutable learner-event ledger, not a new
    mastery store.  It deliberately exposes neither answers nor an
    uncalibrated posterior: until BKT is calibrated, callers receive evidence
    count plus its intentionally broad public interval only.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kc_id: str = Field(min_length=1, max_length=160)
    evidence_state: EvidenceState
    change_signal: ChangeSignal = "none"
    verified_observation_count: int = Field(ge=0)
    model_version: str = Field(min_length=1, max_length=32)
    stage_policy_version: str = Field(min_length=1, max_length=64)


class SubjectLearningStateSnapshot(BaseModel):
    """Deterministic, read-only canonical state for one owner/subject.

    ``source_revision`` is a digest of the *eligible* immutable canonical
    events and the shared BKT parameter set.  Weak, unversioned legacy, and
    differently attributed events are intentionally absent from both the
    evidence computation and this revision.  The projection is rebuilt on
    each read; it is not a second persisted learning truth.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    owner_id: str = Field(min_length=1, max_length=96)
    subject_id: str = Field(min_length=1, max_length=96)
    source_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    param_version: str = Field(min_length=1, max_length=32)
    calibrated: bool
    strong_event_count: int = Field(ge=0)
    knowledge: tuple[SubjectKnowledgeEvidence, ...] = Field(default_factory=tuple, max_length=24)


class LearnerSubjectLearningState(BaseModel):
    """Public canonical BKT state with owner identity intentionally removed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    subject_id: str = Field(min_length=1, max_length=96)
    source_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    param_version: str = Field(min_length=1, max_length=32)
    calibrated: bool
    strong_event_count: int = Field(ge=0)
    knowledge: tuple[SubjectKnowledgeEvidence, ...] = Field(default_factory=tuple, max_length=24)


class LearningGovernanceSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    subject_id: str = Field(min_length=1, max_length=96)
    kc_id: str | None = Field(default=None, min_length=1, max_length=96)
    errors: tuple[ErrorSummary, ...] = ()
    misconceptions: tuple[MisconceptionSummary, ...] = ()
    repairs: tuple[RepairSummary, ...] = ()
    reviews: tuple[ReviewSummary, ...] = ()


class VoidLearnerEventRequest(BaseModel):
    """A bounded request to void a server-held canonical event.

    The client supplies only its intended subject partition and an operational
    reason code.  Target ownership/KCs and the stable correction identity are
    read from the ledger, never accepted from the browser.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    subject_id: str = Field(min_length=1, max_length=96)
    reason_code: AmendmentReason


class LearnerEventAmendmentReceipt(BaseModel):
    """Learner-safe acknowledgement of an immutable canonical void."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    amendment_id: str = Field(min_length=1, max_length=96)
    target_event_id: str = Field(min_length=1, max_length=96)
    action: AmendmentAction
    created_at: str


__all__ = [
    "ErrorSummary",
    "GovernanceAttributionStatus",
    "LearningGovernanceSnapshot",
    "LearnerSubjectLearningState",
    "LearnerEventAmendmentReceipt",
    "MisconceptionSummary",
    "RepairSummary",
    "ReviewStatus",
    "ReviewSummary",
    "SubjectKnowledgeEvidence",
    "SubjectLearningStateSnapshot",
    "VoidLearnerEventRequest",
]
