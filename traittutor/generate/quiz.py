"""Strict, source-grounded quiz batch contracts."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .grounding import (
    GroundingChunk,
    SourceReference,
    StructuredBatchValidationError,
    coerce_grounding_chunks,
    parse_json_object,
    validate_source_references,
)

QuestionType = Literal["OPTIONS", "DELAY_OPTIONS", "TF", "SHORT_ANSWER", "FILL_BLANK"]
Difficulty = Literal["easy", "medium", "hard"]
MAX_QUIZ_QUESTIONS_PER_BATCH = 8


class QuizOption(BaseModel):
    """One selectable answer option."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, strict=True)

    text: str = Field(min_length=1, max_length=300)
    is_correct: bool


class QuizQuestion(BaseModel):
    """One answerable question whose answer is justified by material quotes."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, strict=True)

    node_id: str = Field(min_length=1, max_length=200)
    node_name: str = Field(min_length=1, max_length=160)
    question_id: int = Field(ge=1)
    question: str = Field(min_length=1, max_length=500)
    question_type: QuestionType
    difficulty: Difficulty
    options: list[QuizOption] = Field(max_length=4)
    correct_answer: str = Field(min_length=1, max_length=300)
    explanation: str = Field(min_length=1, max_length=900)
    references: list[SourceReference] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def _require_answerable_structure(self) -> QuizQuestion:
        if re.match(r"^\s*\[(difficulty|type)\s*:", self.question, flags=re.IGNORECASE):
            raise ValueError("question text must not expose difficulty or type labels")
        if not self.explanation.startswith(self.correct_answer):
            raise ValueError("explanation must begin with the exact correct answer")

        option_texts = [option.text.casefold() for option in self.options]
        if len(option_texts) != len(set(option_texts)):
            raise ValueError("question options must be distinct")

        correct_options = [option for option in self.options if option.is_correct]
        if self.question_type in {"OPTIONS", "DELAY_OPTIONS"}:
            if len(self.options) != 4 or len(correct_options) != 1:
                raise ValueError("option questions require exactly four options and one correct answer")
            if self.correct_answer.casefold() != correct_options[0].text.casefold():
                raise ValueError("correct_answer must match the selected correct option")
        elif self.question_type == "TF":
            if len(self.options) != 2 or len(correct_options) != 1:
                raise ValueError("true/false questions require two options and one correct answer")
            if self.correct_answer.casefold() != correct_options[0].text.casefold():
                raise ValueError("correct_answer must match the selected correct option")
            if re.search(r"\b(true|false)\b|是非题|判断题", self.question, flags=re.IGNORECASE):
                raise ValueError("true/false question text must contain only the statement to judge")
        else:
            if self.options:
                raise ValueError("short-answer and fill-blank questions must not include options")
            if self.question_type == "FILL_BLANK" and self.question.count("____") != 1:
                raise ValueError("fill-blank questions require exactly one blank")
        return self


class QuizBatch(BaseModel):
    """The full model payload for one quiz batch."""

    model_config = ConfigDict(extra="forbid", strict=True)

    items: list[QuizQuestion] = Field(min_length=1, max_length=MAX_QUIZ_QUESTIONS_PER_BATCH)

    @model_validator(mode="after")
    def _require_stable_question_order(self) -> QuizBatch:
        question_ids = [item.question_id for item in self.items]
        expected = list(range(question_ids[0], question_ids[0] + len(question_ids)))
        if question_ids != expected:
            raise ValueError("question_id values must be sequential within a batch")
        questions = [" ".join(item.question.casefold().split()) for item in self.items]
        if len(questions) != len(set(questions)):
            raise ValueError("quiz batch contains duplicate questions")
        return self


@dataclass(frozen=True)
class ValidatedQuizBatch:
    """A complete quiz payload safe for incremental presentation."""

    items: tuple[QuizQuestion, ...]
    displayable: Literal[True] = True


@dataclass(frozen=True)
class QuizBatchPlan:
    """A bounded, source-ordered unit of quiz generation work."""

    batch_index: int
    total_batches: int
    source_chunks: tuple[GroundingChunk, ...]
    question_id_start: int
    question_count: int

    @property
    def chunk_ids(self) -> tuple[str, ...]:
        return tuple(chunk.chunk_id for chunk in self.source_chunks)


def plan_quiz_batches(
    chunks: Iterable[GroundingChunk | Mapping[str, Any]],
    *,
    chunks_per_batch: int = 2,
    questions_per_batch: int = 4,
    first_question_id: int = 1,
) -> tuple[QuizBatchPlan, ...]:
    """Split source chunks into small quiz batches with stable ID ranges."""

    if chunks_per_batch < 1:
        raise ValueError("chunks_per_batch must be at least 1")
    if not 1 <= questions_per_batch <= MAX_QUIZ_QUESTIONS_PER_BATCH:
        raise ValueError(
            f"questions_per_batch must be between 1 and {MAX_QUIZ_QUESTIONS_PER_BATCH}"
        )
    if first_question_id < 1:
        raise ValueError("first_question_id must be at least 1")
    normalized = coerce_grounding_chunks(chunks)
    if not normalized:
        return ()
    groups = tuple(
        normalized[index : index + chunks_per_batch]
        for index in range(0, len(normalized), chunks_per_batch)
    )
    total = len(groups)
    return tuple(
        QuizBatchPlan(
            batch_index=index,
            total_batches=total,
            source_chunks=group,
            question_id_start=first_question_id + (index - 1) * questions_per_batch,
            question_count=questions_per_batch,
        )
        for index, group in enumerate(groups, start=1)
    )


def validate_quiz_payload(
    payload: Mapping[str, Any],
    chunks: Iterable[GroundingChunk | Mapping[str, Any]],
) -> QuizBatch:
    """Validate a parsed model payload before a caller exposes its questions."""

    try:
        batch = QuizBatch.model_validate(payload)
        normalized_chunks = coerce_grounding_chunks(chunks)
        for item in batch.items:
            validate_source_references(item.references, normalized_chunks)
    except (ValidationError, ValueError) as exc:
        raise StructuredBatchValidationError("quiz", str(exc)) from exc
    return batch


def validate_quiz_batch(
    raw: str | bytes | bytearray | Mapping[str, Any],
    chunks: Iterable[GroundingChunk | Mapping[str, Any]],
) -> ValidatedQuizBatch:
    """Parse and validate an entire quiz batch before it becomes displayable."""

    payload = parse_json_object(raw, "quiz")
    batch = validate_quiz_payload(payload, chunks)
    return ValidatedQuizBatch(items=tuple(batch.items))


__all__ = [
    "Difficulty",
    "MAX_QUIZ_QUESTIONS_PER_BATCH",
    "QuestionType",
    "QuizBatch",
    "QuizBatchPlan",
    "QuizOption",
    "QuizQuestion",
    "ValidatedQuizBatch",
    "plan_quiz_batches",
    "validate_quiz_batch",
    "validate_quiz_payload",
]
