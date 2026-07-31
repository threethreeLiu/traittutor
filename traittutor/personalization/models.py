"""Strict data contracts for the TraitTutor learner model.

These models deliberately describe teaching signals, never diagnosis, ability,
learning-style, mood, or mutable personality claims.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class SubjectRef(BaseModel):
    subject_id: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=120)
    path: list[str] = Field(default_factory=list, max_length=6)
    confidence: float = Field(ge=0, le=1)
    source: Literal["user", "material_analysis", "artifact", "rule", "llm"]
    confirmed: bool = False


class PreferenceEvidence(BaseModel):
    id: str
    value: str = Field(min_length=1, max_length=240)
    category: Literal["goal", "explanation", "pacing", "feedback", "constraint"]
    state: Literal["explicit", "inferred", "rejected"]
    confidence: float = Field(ge=0, le=1)
    evidence_refs: list[str] = Field(default_factory=list, max_length=24)
    updated_at: str
    expires_at: str | None = None


class ReflectionView(BaseModel):
    """A user-governed learner-memory reflection.

    Reflections are the visible layer over profile evidence.  Candidate
    reflections can be shown to the learner without being injected into the
    Compass/personalization context until they are explicitly confirmed.
    """

    reflection_id: str
    scope: Literal["global", "subject"]
    subject: SubjectRef | None = None
    category: Literal["goal", "explanation", "pacing", "feedback", "constraint", "concept", "strategy"]
    value: str = Field(min_length=1, max_length=260)
    status: Literal["candidate", "confirmed", "rejected", "stale", "needs_rebuild"]
    source_state: Literal["explicit", "inferred", "rejected"] | None = None
    confidence: float = Field(ge=0, le=1)
    evidence_refs: list[str] = Field(default_factory=list, max_length=24)
    updated_at: str
    expires_at: str | None = None
    applies_to_compass: bool = False
    reason: str = Field(default="", max_length=220)


class ConceptSignal(BaseModel):
    concept_id: str = Field(min_length=1, max_length=160)
    label: str = Field(min_length=1, max_length=160)
    support_level: Literal["needs_support", "developing", "supported"]
    confidence: float = Field(ge=0, le=1)
    attempt_count: int = Field(ge=0)
    misconception_tags: list[str] = Field(default_factory=list, max_length=12)
    evidence_refs: list[str] = Field(default_factory=list, max_length=24)
    last_practised_at: str | None = None
    # Knowledge-tracing state.  These are observations of learning activity,
    # not a measure of innate ability or a learner diagnosis.
    module_id: str | None = None
    mastery_probability: float = Field(default=0.2, ge=0, le=1)
    initial_mastery_probability: float = Field(default=0.2, ge=0, le=1)
    transition_probability: float = Field(default=0.12, ge=0, le=1)
    guess_probability: float = Field(default=0.2, ge=0, le=1)
    slip_probability: float = Field(default=0.1, ge=0, le=1)
    observation_count: int = Field(default=0, ge=0)
    verified_observation_count: int = Field(default=0, ge=0)
    last_observation_source: str | None = None


class SubjectUnderstanding(BaseModel):
    """Derived, explainable summary of the concepts in one subject."""

    status: Literal["starting", "learning", "familiar", "verified"] = "starting"
    concept_count: int = Field(default=0, ge=0)
    observed_concept_count: int = Field(default=0, ge=0)
    coverage: float = Field(default=0, ge=0, le=1)
    verified_mastery: float = Field(default=0, ge=0, le=1)
    confidence: float = Field(default=0, ge=0, le=1)
    recent_activity_at: str | None = None
    review_load: int = Field(default=0, ge=0)


class TeachingAction(BaseModel):
    structure: Literal["outline", "worked_example", "comparison", "narrative"] = "outline"
    scaffolding: Literal["low", "medium", "high"] = "medium"
    example_style: list[Literal["code", "case", "analogy", "visual"]] = Field(default_factory=list)
    pacing: Literal["compact", "standard", "stepwise"] = "standard"
    challenge: Literal["foundation", "standard", "stretch"] = "standard"
    feedback: Literal["direct", "hint_first", "reflective"] = "hint_first"
    interaction: Literal["explain_first", "ask_first", "practice_first"] = "explain_first"


class StrategyEvidence(BaseModel):
    id: str
    strategy: TeachingAction
    task_type: Literal["chat", "courseware", "flashcards", "quiz"]
    positive_weight: float = Field(ge=0)
    negative_weight: float = Field(ge=0)
    confidence: float = Field(ge=0, le=1)
    evidence_refs: list[str] = Field(default_factory=list, max_length=24)
    # Signal ids, rather than page views or saves, make the three-event
    # threshold auditable and prevent a single event from becoming a trait.
    event_ids: list[str] = Field(default_factory=list, max_length=48)
    last_observed_at: str


class LearnerProfile(BaseModel):
    owner_id: str
    scope: Literal["global", "subject"]
    subject: SubjectRef | None = None
    inference_enabled: bool = True
    preferences: list[PreferenceEvidence] = Field(default_factory=list)
    concept_signals: list[ConceptSignal] = Field(default_factory=list)
    strategy_evidence: list[StrategyEvidence] = Field(default_factory=list)
    understanding: SubjectUnderstanding | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    schema_version: int = 1
    updated_at: str
    needs_rebuild: bool = False


class LearningSignal(BaseModel):
    signal_id: str = Field(min_length=1, max_length=128)
    owner_id: str | None = Field(default=None, min_length=1, max_length=160)
    kind: Literal["explicit_preference", "goal", "artifact_outcome", "quiz_attempt", "strategy_feedback", "misconception", "subject_correction", "learner_event", "reflection_decision"]
    subject_refs: list[SubjectRef] = Field(default_factory=list, max_length=5)
    payload: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list, max_length=24)
    source: Literal["user", "system", "validated_model"]
    occurred_at: str

    @field_validator("payload")
    @classmethod
    def reject_sensitive_payload_keys(cls, value: dict[str, Any]) -> dict[str, Any]:
        prohibited = {"iq", "intelligence", "ability", "diagnosis", "personality_score", "learning_style", "mood"}
        if prohibited.intersection(key.lower() for key in value):
            raise ValueError("learning signals cannot contain diagnosis, ability, personality, or learning-style fields")
        return value


class LearnerEvent(BaseModel):
    """The normalized, idempotent input to the learning-model updater.

    It intentionally retains references rather than copying raw chat or memory
    content.  ``observation`` is only interpreted as a mastery observation for
    the explicitly high-confidence activity types below.
    """

    event_id: str = Field(min_length=1, max_length=128)
    event_type: Literal[
        "mastery_attempt", "quiz_answer", "flashcard_review", "courseware_outcome",
        "chat_correction", "self_assessment", "memory_preference", "memory_candidate",
        "strategy_feedback",
    ]
    subject: SubjectRef | None = None
    concept_id: str | None = Field(default=None, max_length=160)
    concept_label: str | None = Field(default=None, max_length=160)
    module_id: str | None = Field(default=None, max_length=160)
    observation: Literal["correct", "incorrect", "known", "unknown", "uncertain", "engaged"] | None = None
    confidence: float = Field(default=0.5, ge=0, le=1)
    evidence_refs: list[str] = Field(default_factory=list, max_length=24)
    payload: dict[str, Any] = Field(default_factory=dict)
    occurred_at: str

    @field_validator("payload")
    @classmethod
    def reject_sensitive_event_payload(cls, value: dict[str, Any]) -> dict[str, Any]:
        return LearningSignal.reject_sensitive_payload_keys(value)


class VisibleRationale(BaseModel):
    source: Literal["current_instruction", "explicit_preference", "strategy_evidence", "concept_signal", "personality_prior", "default"]
    text: str = Field(min_length=1, max_length=220)
    evidence_refs: list[str] = Field(default_factory=list, max_length=12)


class TeachingStrategyPlan(TeachingAction):
    srl_support: list[Literal["goal", "monitor", "reflect", "motivation"]] = Field(default_factory=list)
    rationale: list[VisibleRationale] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)


class LearnerMemorySnapshot(BaseModel):
    """Small, versioned learner memory frame for one conversation.

    This is intentionally a curated view over the auditable learner profile,
    rather than another source of truth.  It keeps a session's durable
    preferences stable while concept-level BKT observations are retrieved live.
    """

    version: int = Field(default=1, ge=1)
    snapshot_id: str = Field(min_length=12, max_length=96)
    created_at: str
    goals: list[str] = Field(default_factory=list, max_length=2)
    explicit_preferences: list[str] = Field(default_factory=list, max_length=6)
    constraints: list[str] = Field(default_factory=list, max_length=4)
    evidence_refs: list[str] = Field(default_factory=list, max_length=24)


class KnowledgeGraphNode(BaseModel):
    """A source-grounded concept; BKT observations attach to this stable id."""

    concept_id: str = Field(min_length=1, max_length=160)
    label: str = Field(min_length=1, max_length=160)
    module_id: str = Field(min_length=1, max_length=160)
    module_label: str = Field(min_length=1, max_length=160)
    evidence_chunk_ids: list[str] = Field(min_length=1, max_length=4)
    confidence: float = Field(ge=0, le=1)


class KnowledgeGraphEdge(BaseModel):
    """A directed relationship; only prerequisite edges shape learning order."""

    source_concept_id: str = Field(min_length=1, max_length=160)
    target_concept_id: str = Field(min_length=1, max_length=160)
    relation: Literal["prerequisite", "part_of", "related_to"]
    evidence_chunk_ids: list[str] = Field(min_length=1, max_length=4)
    confidence: float = Field(ge=0, le=1)


class LearningKnowledgeGraph(BaseModel):
    version: int = Field(default=1, ge=1)
    subject: SubjectRef
    nodes: list[KnowledgeGraphNode] = Field(default_factory=list, max_length=80)
    edges: list[KnowledgeGraphEdge] = Field(default_factory=list, max_length=180)
    source_refs: list[str] = Field(default_factory=list, max_length=24)
    updated_at: str


class PersonalizationContext(BaseModel):
    version: int = 1
    purpose: Literal["chat", "courseware", "flashcards", "quiz"]
    subject: SubjectRef | None = None
    active_goal: str | None = None
    plan: TeachingStrategyPlan
    memory_snapshot: LearnerMemorySnapshot | None = None
    relevant_concept_signals: list[ConceptSignal] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    trace_id: str
    degraded: bool = False
    degradation_reason: str | None = None
