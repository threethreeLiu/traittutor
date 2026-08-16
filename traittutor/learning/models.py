from __future__ import annotations

from enum import Enum
import time
from typing import Any
import uuid

from pydantic import BaseModel, ConfigDict, Field

_KNOWLEDGE_TYPE_LEGACY: dict[str, str] = {
    "记忆型": "memory",
    "概念型": "concept",
    "程序型": "procedure",
    "设计型": "design",
}

_ERROR_TYPE_LEGACY: dict[str, str] = {
    "知识结构性": "structural",
    "理解偏差型": "deviation",
    "应用错误": "application",
    "元认知型": "metacognitive",
}


class KnowledgeType(str, Enum):
    MEMORY = "memory"
    CONCEPT = "concept"
    PROCEDURE = "procedure"
    DESIGN = "design"

    @classmethod
    def _missing_(cls, value: object) -> KnowledgeType | None:
        mapped = _KNOWLEDGE_TYPE_LEGACY.get(str(value))
        return cls(mapped) if mapped else None


class ErrorType(str, Enum):
    KNOWLEDGE_STRUCTURAL = "structural"
    UNDERSTANDING_DEVIATION = "deviation"
    APPLICATION_ERROR = "application"
    METACOGNITIVE = "metacognitive"

    @classmethod
    def _missing_(cls, value: object) -> ErrorType | None:
        mapped = _ERROR_TYPE_LEGACY.get(str(value))
        return cls(mapped) if mapped else None


_ERROR_STATUS_LEGACY: dict[str, str] = {
    "active": "open",
    "retrying": "open",
    "review": "open",
    "graduated": "repaired",
}


class ErrorRecordStatus(str, Enum):
    """Auditable error lifecycle; repaired records remain in history."""

    OPEN = "open"
    REPAIRED = "repaired"
    RELAPSED = "relapsed"

    @classmethod
    def _missing_(cls, value: object) -> ErrorRecordStatus | None:
        mapped = _ERROR_STATUS_LEGACY.get(str(value))
        return cls(mapped) if mapped else None


# Stages removed in the Mastery Path simplification are mapped onto the nearest
# surviving stage so progress persisted by the older engine still deserializes.
_STAGE_LEGACY: dict[str, str] = {
    "diagnostic_phase1": "diagnostic",
    "diagnostic_phase2": "diagnostic",
    "metacognitive_intro": "explain",
    "plan": "explain",
    "pretest": "explain",
    "practice_quiz": "practice",
    "module_test": "review",
}


class LearningStage(str, Enum):
    """The Mastery Path loop: diagnose once, then per knowledge point teach and
    check understanding, then practice the module, diagnose errors, and schedule
    spaced review."""

    DIAGNOSTIC = "diagnostic"
    EXPLAIN = "explain"
    FEYNMAN_CHECK = "feynman_check"
    PRACTICE = "practice"
    ERROR_DIAGNOSIS = "error_diagnosis"
    REVIEW = "review"
    COMPLETED = "completed"

    @classmethod
    def _missing_(cls, value: object) -> LearningStage | None:
        mapped = _STAGE_LEGACY.get(str(value))
        return cls(mapped) if mapped else None


class KnowledgePoint(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    type: KnowledgeType
    module_id: str


class LearningModule(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    order: int
    pass_threshold: float = 0.7
    knowledge_points: list[KnowledgePoint] = Field(default_factory=list)


class DiagnosticResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    total_questions: int = 0
    correct_count: int = 0
    module_mastery: dict[str, float] = Field(default_factory=dict)


class QuizAttempt(BaseModel):
    model_config = ConfigDict(extra="ignore")

    question_id: str
    knowledge_point_id: str
    module_id: str = ""
    is_correct: bool
    user_answer: Any = None
    error_type: ErrorType | None = None
    self_attribution: str = ""
    mastery_estimate: float = 0.0
    timestamp: float = Field(default_factory=time.time)
    # Additive migration fields. Stable identities let the canonical event
    # chain retry a partially-derived write without duplicating an attempt.
    event_id: str | None = None
    idempotency_key: str | None = None


class RetryAttempt(BaseModel):
    model_config = ConfigDict(extra="ignore")

    timestamp: float
    is_correct: bool
    attempt_number: int
    event_id: str | None = None


class ErrorRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    question_id: str
    knowledge_point_id: str
    module_id: str
    error_type: ErrorType
    self_attribution: str = ""
    ai_confirmation: str = ""
    retry_history: list[RetryAttempt] = Field(default_factory=list)
    # These are bounded recent-detail projections. The immutable learner-event
    # ledger is the audit source; lifetime totals remain available below.
    total_retry_count: int = Field(default=0, ge=0)
    status: ErrorRecordStatus = ErrorRecordStatus.OPEN
    source_event_ids: list[str] = Field(default_factory=list)
    total_source_event_count: int = Field(default=0, ge=0)
    lifecycle_version: int = Field(default=2, ge=2)
    created_at: float = Field(default_factory=time.time)
    repaired_at: float | None = None
    relapsed_at: float | None = None
    last_seen_at: float | None = None


class RepetitionState(BaseModel):
    model_config = ConfigDict(extra="ignore")

    interval_index: int = 0
    consecutive_correct: int = 0
    consecutive_wrong: int = 0
    next_review_at: float


class ReviewTask(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    knowledge_point_id: str
    knowledge_type: KnowledgeType
    due_at: float
    priority: int
    state: RepetitionState


class PendingQuestion(BaseModel):
    """A question posed to the learner and awaiting their answer.

    Persisted so grading is deterministic across turns: the expected answer
    lives here server-side and never round-trips through the model. The tutor
    poses a question with ``mastery_quiz`` (storing this), the learner answers
    on a later turn, and ``mastery_grade`` scores the stored answer.
    """

    model_config = ConfigDict(extra="ignore")

    question_id: str
    # Issued with the pending question so retries of one submission are stable
    # while a newly posed attempt receives a distinct identity.
    attempt_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    knowledge_point_id: str
    module_id: str = ""
    prompt: str = ""
    question_type: str = "short"
    expected_answer: str = ""
    options: list[str] = Field(default_factory=list)
    created_at: float = Field(default_factory=time.time)


class LearningProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    book_id: str
    # Set only from a reliably attributed canonical answer event. It is never
    # inferred from book/module labels, so policy reads cannot cross subjects.
    subject_id: str = ""
    diagnostic: DiagnosticResult | None = None
    modules: list[LearningModule] = Field(default_factory=list)
    current_module_id: str = ""
    current_stage: LearningStage = LearningStage.DIAGNOSTIC
    current_kp_index: int = 0
    # Canonical display evidence. Uncalibrated BKT remains unknown.
    verified_observation_counts: dict[str, int] = Field(default_factory=dict)
    mastery_intervals: dict[str, tuple[float, float]] = Field(default_factory=dict)
    # Qualitative gate for CONCEPT / DESIGN knowledge points: True once the
    # tutor judges the learner's explanation sufficient (``mastery_assess``).
    # MEMORY / PROCEDURE are read exclusively from canonical BKT.
    qualitative_mastery: dict[str, bool] = Field(default_factory=dict)
    knowledge_types: dict[str, KnowledgeType] = Field(default_factory=dict)
    # Display projection of recent attempts, bounded for serialization size.
    quiz_attempts: list[QuizAttempt] = Field(default_factory=list)
    # Unbounded replay guard: quiz_attempts is trimmed, so deduplication must
    # not rely on it. One event_id entry per derived attempt, never trimmed.
    seen_quiz_event_ids: list[str] = Field(default_factory=list)
    error_records: list[ErrorRecord] = Field(default_factory=list)
    repetition_states: dict[str, RepetitionState] = Field(default_factory=dict)
    review_queue: list[ReviewTask] = Field(default_factory=list)
    # A single outstanding question; grading reads its expected answer so the
    # model never has to recall it across turns.
    pending_question: PendingQuestion | None = None
    # Recovery is deliberately separate from mastery evidence.  A deferred KC
    # remains unmastered and keeps every BKT/error/review fact; policy merely
    # stops selecting it until a trusted attempt on a different KC occurs.
    objective_attempt_sequence: int = Field(default=0, ge=0)
    consecutive_failures_by_kc: dict[str, int] = Field(default_factory=dict)
    deferred_knowledge_points: dict[str, int] = Field(default_factory=dict)
    feynman_retries: dict[str, int] = Field(default_factory=dict)
    feynman_explanations: dict[str, str] = Field(default_factory=dict)
    stage_failure_counts: dict[str, int] = Field(default_factory=dict)
    stage_failure_notes: dict[str, str] = Field(default_factory=dict)
    version: int = 0
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


__all__ = [
    "KnowledgeType",
    "ErrorType",
    "ErrorRecordStatus",
    "LearningStage",
    "KnowledgePoint",
    "LearningModule",
    "DiagnosticResult",
    "QuizAttempt",
    "RetryAttempt",
    "ErrorRecord",
    "RepetitionState",
    "ReviewTask",
    "PendingQuestion",
    "LearningProgress",
]
