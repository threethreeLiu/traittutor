"""Learner-safe page read models for the WS-16 learning profile.

These DTOs are projections over existing canonical stores.  They deliberately
contain no owner identifier, answer, rubric, raw event body, prompt, or
uncalibrated BKT posterior.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .stage_policy import (
    EVIDENCE_STAGE_POLICY_VERSION,
    ChangeSignal,
    EvidenceState,
)


class SectionStatus(str, Enum):
    READY = "ready"
    EMPTY = "empty"
    UNAVAILABLE = "unavailable"
    STALE = "stale"
    REBUILDING = "rebuilding"


class SectionMeta(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: SectionStatus
    updated_at: str | None = None
    source_refs: tuple[str, ...] = Field(default_factory=tuple, max_length=24)
    unavailable_sources: tuple[str, ...] = Field(default_factory=tuple, max_length=12)


class MasteryDisplay(BaseModel):
    """Qualitative public evidence state; private posterior never crosses this DTO."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_state: EvidenceState = "insufficient_evidence"
    change_signal: ChangeSignal = "none"
    verified_observation_count: int = Field(default=0, ge=0)
    model_version: str | None = Field(default=None, max_length=64)
    stage_policy_version: str = Field(default=EVIDENCE_STAGE_POLICY_VERSION, max_length=64)


class KcMasteryDisplay(MasteryDisplay):
    kc_id: str = Field(min_length=1, max_length=160)


class SubjectCard(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    subject_id: str = Field(min_length=1, max_length=96)
    label: str = Field(min_length=1, max_length=120)
    data_status: SectionStatus
    last_activity_at: str | None = None
    covered_kc_count: int = Field(default=0, ge=0)
    strong_evidence_count: int = Field(default=0, ge=0)
    open_error_count: int = Field(default=0, ge=0)
    due_review_count: int = Field(default=0, ge=0)
    source_refs: tuple[str, ...] = Field(default_factory=tuple, max_length=24)


class PendingSubjectCard(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    subject_id: str = Field(min_length=1, max_length=96)
    label: str = Field(min_length=1, max_length=120)
    created_at: str | None = None
    source_refs: tuple[str, ...] = Field(default_factory=tuple, max_length=24)
    possible_duplicate_subject_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=12)


class LearningTask(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str = Field(min_length=1, max_length=192)
    subject_id: str = Field(min_length=1, max_length=96)
    kind: Literal["review", "error_repair", "attribution"]
    due_at: str | None = None
    source_refs: tuple[str, ...] = Field(default_factory=tuple, max_length=12)


class TodaySummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    meta: SectionMeta
    active_subject_count: int = Field(default=0, ge=0)
    due_review_count: int = Field(default=0, ge=0)
    open_error_count: int = Field(default=0, ge=0)
    attribution_pending_count: int = Field(default=0, ge=0)
    latest_activity_at: str | None = None


class SubjectsSection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    meta: SectionMeta
    items: tuple[SubjectCard, ...] = ()


class PendingSubjectsSection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    meta: SectionMeta
    items: tuple[PendingSubjectCard, ...] = ()


class TaskQueueSection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    meta: SectionMeta
    items: tuple[LearningTask, ...] = ()


class SupportSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    meta: SectionMeta
    inference_enabled: bool | None = None
    confirmed_preference_count: int = Field(default=0, ge=0)
    confirmed_reflection_count: int = Field(default=0, ge=0)
    compass_signal_count: int = Field(default=0, ge=0)


class LearningModelOverview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    generated_at: str
    today: TodaySummary
    confirmed_subjects: SubjectsSection
    pending_subjects: PendingSubjectsSection
    task_queue: TaskQueueSection
    support: SupportSummary


class SubjectHeader(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    subject_id: str = Field(min_length=1, max_length=96)
    label: str = Field(min_length=1, max_length=120)
    confirmed: bool
    updated_at: str | None = None
    data_status: SectionStatus


class SubjectTabSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    meta: SectionMeta
    item_count: int = Field(default=0, ge=0)
    actionable_count: int = Field(default=0, ge=0)


class KnowledgeTabSummary(SubjectTabSummary):
    mastery_items: tuple[KcMasteryDisplay, ...] = Field(default_factory=tuple, max_length=24)
    model_version: str | None = Field(default=None, max_length=64)
    mapping_version: str | None = Field(default=None, max_length=64)


class SubjectTabs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    overview: SubjectTabSummary
    knowledge: KnowledgeTabSummary
    errors: SubjectTabSummary
    reviews: SubjectTabSummary
    misconceptions: SubjectTabSummary
    support: SubjectTabSummary
    governance: SubjectTabSummary


class LearningModelSubjectDetail(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    generated_at: str
    header: SubjectHeader
    tabs: SubjectTabs
    allowed_actions: tuple[
        Literal[
            "continue_learning",
            "start_review",
            "repair_error",
            "confirm_subject",
            "correct_subject",
            "view_evidence",
        ],
        ...,
    ] = ()


__all__ = [
    "KnowledgeTabSummary",
    "KcMasteryDisplay",
    "LearningModelOverview",
    "LearningModelSubjectDetail",
    "LearningTask",
    "MasteryDisplay",
    "PendingSubjectCard",
    "PendingSubjectsSection",
    "SectionMeta",
    "SectionStatus",
    "SubjectCard",
    "SubjectHeader",
    "SubjectsSection",
    "SubjectTabSummary",
    "SubjectTabs",
    "SupportSummary",
    "TaskQueueSection",
    "TodaySummary",
]
