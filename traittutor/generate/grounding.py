"""Shared source-grounding contracts for structured generation batches."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class GroundingChunk(BaseModel):
    """A material slice that a generated item may cite exactly."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, strict=True)

    source_id: str = Field(min_length=1, max_length=200)
    chunk_id: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1)


class SourceReference(BaseModel):
    """A source quote that can be verified against a resolved material chunk."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, strict=True)

    source_id: str = Field(min_length=1, max_length=200)
    chunk_id: str = Field(min_length=1, max_length=200)
    text_snippet: str = Field(min_length=1, max_length=500)


class StructuredBatchValidationError(ValueError):
    """An output error that must never be rendered as a completed batch."""

    displayable = False

    def __init__(self, batch_kind: str, *errors: str) -> None:
        self.batch_kind = batch_kind
        self.errors = tuple(error for error in errors if error)
        detail = "; ".join(self.errors) or "invalid structured output"
        super().__init__(f"{batch_kind} batch is not displayable: {detail}")


def parse_json_object(raw: str | bytes | bytearray | Mapping[str, Any], batch_kind: str) -> dict[str, Any]:
    """Parse one complete JSON object without accepting partial model output."""

    if isinstance(raw, (str, bytes, bytearray)):
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise StructuredBatchValidationError(batch_kind, "response is not complete JSON") from exc
    elif isinstance(raw, Mapping):
        payload = dict(raw)
    else:
        raise StructuredBatchValidationError(batch_kind, "response must be a JSON object")
    if not isinstance(payload, dict):
        raise StructuredBatchValidationError(batch_kind, "response must be a JSON object")
    return payload


def coerce_grounding_chunks(
    chunks: Iterable[GroundingChunk | Mapping[str, Any]],
) -> tuple[GroundingChunk, ...]:
    """Normalize material chunks while rejecting ambiguous source/chunk pairs."""

    normalized: list[GroundingChunk] = []
    seen: set[tuple[str, str]] = set()
    for index, chunk in enumerate(chunks, start=1):
        try:
            parsed = chunk if isinstance(chunk, GroundingChunk) else GroundingChunk.model_validate(chunk)
        except ValidationError as exc:
            raise ValueError(f"invalid grounding chunk {index}: {exc}") from exc
        key = (parsed.source_id, parsed.chunk_id)
        if key in seen:
            raise ValueError(f"duplicate grounding chunk {parsed.source_id!r}/{parsed.chunk_id!r}")
        seen.add(key)
        normalized.append(parsed)
    return tuple(normalized)


def validate_source_references(
    references: Sequence[SourceReference],
    chunks: Iterable[GroundingChunk | Mapping[str, Any]],
) -> None:
    """Ensure every reference names a supplied chunk and quotes it verbatim."""

    chunk_index = {
        (chunk.source_id, chunk.chunk_id): chunk for chunk in coerce_grounding_chunks(chunks)
    }
    if not chunk_index:
        raise ValueError("source-grounded output requires at least one material chunk")

    seen: set[tuple[str, str, str]] = set()
    for reference in references:
        key = (reference.source_id, reference.chunk_id)
        chunk = chunk_index.get(key)
        if chunk is None:
            raise ValueError(
                f"reference points to unavailable chunk {reference.source_id!r}/{reference.chunk_id!r}"
            )
        normalized_quote = _normalize_text(reference.text_snippet)
        if normalized_quote not in _normalize_text(chunk.text):
            raise ValueError(
                f"reference quote is not present in chunk {reference.source_id!r}/{reference.chunk_id!r}"
            )
        reference_key = (*key, normalized_quote)
        if reference_key in seen:
            raise ValueError("reference entries must not repeat the same source quote")
        seen.add(reference_key)


def _normalize_text(value: str) -> str:
    return " ".join(value.split())


__all__ = [
    "GroundingChunk",
    "SourceReference",
    "StructuredBatchValidationError",
    "coerce_grounding_chunks",
    "parse_json_object",
    "validate_source_references",
]
