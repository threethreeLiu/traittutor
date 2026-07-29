"""Strict, source-grounded flashcard batch contracts."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .grounding import (
    GroundingChunk,
    SourceReference,
    StructuredBatchValidationError,
    coerce_grounding_chunks,
    parse_json_object,
    validate_source_references,
)

MAX_FLASHCARDS_PER_BATCH = 5


class Flashcard(BaseModel):
    """One atomic active-recall prompt anchored to source material."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, strict=True)

    node_id: str = Field(min_length=1, max_length=200)
    node_name: str = Field(min_length=1, max_length=160)
    front: str = Field(min_length=1, max_length=120)
    back: str = Field(min_length=1, max_length=280)
    references: list[SourceReference] = Field(min_length=1, max_length=3)

    @field_validator("front", "back")
    @classmethod
    def _require_single_line(cls, value: str) -> str:
        if "\n" in value or "\r" in value:
            raise ValueError("flashcard text must be a single line")
        return value

    @model_validator(mode="after")
    def _require_atomic_recall(self) -> Flashcard:
        if len(re.findall(r"[?？]", self.front)) > 1:
            raise ValueError("flashcard front must ask about one recall target")
        if any(separator in self.front for separator in (";", "；")):
            raise ValueError("flashcard front must not combine multiple recall targets")
        return self


class FlashcardBatch(BaseModel):
    """The full model payload for one flashcard batch."""

    model_config = ConfigDict(extra="forbid", strict=True)

    items: list[Flashcard] = Field(min_length=1, max_length=MAX_FLASHCARDS_PER_BATCH)

    @model_validator(mode="after")
    def _reject_duplicate_recall_targets(self) -> FlashcardBatch:
        fronts = [" ".join(item.front.casefold().split()) for item in self.items]
        if len(fronts) != len(set(fronts)):
            raise ValueError("flashcard batch contains duplicate recall targets")
        return self


@dataclass(frozen=True)
class ValidatedFlashcardBatch:
    """A complete flashcard payload safe for incremental presentation."""

    items: tuple[Flashcard, ...]
    displayable: Literal[True] = True


@dataclass(frozen=True)
class FlashcardBatchPlan:
    """A bounded, source-ordered unit of flashcard generation work."""

    batch_index: int
    total_batches: int
    source_chunks: tuple[GroundingChunk, ...]
    item_limit: int

    @property
    def chunk_ids(self) -> tuple[str, ...]:
        return tuple(chunk.chunk_id for chunk in self.source_chunks)


def plan_flashcard_batches(
    chunks: Iterable[GroundingChunk | Mapping[str, Any]],
    *,
    chunks_per_batch: int = 3,
    cards_per_batch: int = MAX_FLASHCARDS_PER_BATCH,
) -> tuple[FlashcardBatchPlan, ...]:
    """Split resolved source chunks into small, source-ordered card batches."""

    if chunks_per_batch < 1:
        raise ValueError("chunks_per_batch must be at least 1")
    if not 1 <= cards_per_batch <= MAX_FLASHCARDS_PER_BATCH:
        raise ValueError(f"cards_per_batch must be between 1 and {MAX_FLASHCARDS_PER_BATCH}")
    normalized = coerce_grounding_chunks(chunks)
    if not normalized:
        return ()
    groups = tuple(
        normalized[index : index + chunks_per_batch]
        for index in range(0, len(normalized), chunks_per_batch)
    )
    total = len(groups)
    return tuple(
        FlashcardBatchPlan(
            batch_index=index,
            total_batches=total,
            source_chunks=group,
            item_limit=cards_per_batch,
        )
        for index, group in enumerate(groups, start=1)
    )


def validate_flashcard_payload(
    payload: Mapping[str, Any],
    chunks: Iterable[GroundingChunk | Mapping[str, Any]],
) -> FlashcardBatch:
    """Validate a parsed model payload before a caller exposes its cards."""

    try:
        batch = FlashcardBatch.model_validate(payload)
        normalized_chunks = coerce_grounding_chunks(chunks)
        for item in batch.items:
            validate_source_references(item.references, normalized_chunks)
    except (ValidationError, ValueError) as exc:
        raise StructuredBatchValidationError("flashcard", str(exc)) from exc
    return batch


def validate_flashcard_batch(
    raw: str | bytes | bytearray | Mapping[str, Any],
    chunks: Iterable[GroundingChunk | Mapping[str, Any]],
) -> ValidatedFlashcardBatch:
    """Parse and validate an entire card batch before it becomes displayable."""

    payload = parse_json_object(raw, "flashcard")
    batch = validate_flashcard_payload(payload, chunks)
    return ValidatedFlashcardBatch(items=tuple(batch.items))


__all__ = [
    "Flashcard",
    "FlashcardBatch",
    "FlashcardBatchPlan",
    "MAX_FLASHCARDS_PER_BATCH",
    "ValidatedFlashcardBatch",
    "plan_flashcard_batches",
    "validate_flashcard_batch",
    "validate_flashcard_payload",
]
