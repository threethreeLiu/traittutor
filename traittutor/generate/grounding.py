"""Shared source-grounding contracts for structured generation batches."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from copy import deepcopy
from difflib import SequenceMatcher
import json
import re
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


_SOURCE_METADATA_ASSESSMENT_PATTERNS = (
    re.compile(
        r"(?:上传(?:的)?(?:文件|文档|材料)?|附件).{0,32}"
        r"(?:文件名|名称|标题|扩展名|格式|路径|编号|id|页码|第几页)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:该|此|这个|上述)(?:文件|文档|材料).{0,24}"
        r"(?:文件名|名称|标题|扩展名|格式|路径|编号|id|页码|第几页)"
        r".{0,16}(?:是什么|属于|哪个|哪一|多少|正确|错误)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:什么|哪个|哪一|多少|正确|错误|是否).{0,16}"
        r"(?:该|此|这个|上述)(?:文件|文档|材料).{0,24}"
        r"(?:文件名|名称|标题|扩展名|格式|路径|编号|id|页码|第几页)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:文件名|上传方式|附件[ _-]?id|来源[ _-]?id|"
        r"分块[ _-]?id|页码|第几页).{0,32}(?:是什么|属于|哪个|哪一|多少|正确|错误)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:uploaded?\s+(?:file|document|material)|attachment)\b.{0,64}"
        r"\b(?:file\s*name|name|title|extension|format|path|id|number)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:what|which)\b.{0,48}\b(?:source|chunk)\s+(?:id|number)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:what|which)\b.{0,48}\bpage\s+number\b.{0,64}"
        r"\b(?:uploaded?\s+(?:file|document|material)|attachment|source|chunk)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:what|which)\b.{0,64}\b(?:name|title|extension|format|path|id|number)\b"
        r".{0,64}\b(?:uploaded?\s+(?:file|document|material)|attachment|source|chunk|page)\b",
        re.IGNORECASE,
    ),
)


def is_source_metadata_assessment(value: str) -> bool:
    """Return whether learner-visible text tests source-container metadata."""

    normalized = " ".join(value.split())
    return any(pattern.search(normalized) for pattern in _SOURCE_METADATA_ASSESSMENT_PATTERNS)


def parse_json_object(
    raw: str | bytes | bytearray | Mapping[str, Any], batch_kind: str
) -> dict[str, Any]:
    """Parse one complete JSON object without accepting partial model output."""

    if isinstance(raw, (str, bytes, bytearray)):
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise StructuredBatchValidationError(
                batch_kind, "response is not complete JSON"
            ) from exc
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
            parsed = (
                chunk if isinstance(chunk, GroundingChunk) else GroundingChunk.model_validate(chunk)
            )
        except ValidationError as exc:
            raise ValueError(f"invalid grounding chunk {index}: {exc}") from exc
        key = (parsed.source_id, parsed.chunk_id)
        if key in seen:
            raise ValueError(f"duplicate grounding chunk {parsed.source_id!r}/{parsed.chunk_id!r}")
        seen.add(key)
        normalized.append(parsed)
    return tuple(normalized)


def _matching_chunk(
    reference: Mapping[str, Any],
    item: Mapping[str, Any],
    chunks: tuple[GroundingChunk, ...],
) -> GroundingChunk | None:
    """Resolve only an unambiguous server-owned source/chunk pair."""

    source_id = str(reference.get("source_id") or "").strip()
    chunk_id = str(reference.get("chunk_id") or item.get("node_id") or "").strip()
    matches = [
        chunk
        for chunk in chunks
        if (not source_id or chunk.source_id == source_id or chunk.source_id.startswith(source_id))
        and (not chunk_id or chunk.chunk_id == chunk_id or chunk.chunk_id.startswith(chunk_id))
    ]
    return matches[0] if len(matches) == 1 else None


def _reference_excerpt(chunk_text: str, proposed: str) -> str:
    """Return a real source excerpt, preferring the closest exact sentence."""

    normalized_proposed = " ".join(proposed.split())
    normalized_chunk = " ".join(chunk_text.split())
    if normalized_proposed and normalized_proposed in normalized_chunk:
        return proposed

    candidates = [
        value.strip() for value in re.split(r"(?<=[。！？.!?；;])|\n+", chunk_text) if value.strip()
    ]
    if normalized_proposed and candidates:
        closest = max(
            candidates,
            key=lambda value: SequenceMatcher(
                None, normalized_proposed, " ".join(value.split())
            ).ratio(),
        )
        score = SequenceMatcher(None, normalized_proposed, " ".join(closest.split())).ratio()
        if score >= 0.72:
            return closest[:500]

    # The fallback is still an exact server-owned quote. It never turns model
    # prose into evidence; it only anchors the item to the supplied chunk.
    return chunk_text.strip()[:500]


def repair_payload_references(
    payload: Mapping[str, Any],
    chunks: Iterable[GroundingChunk | Mapping[str, Any]],
) -> dict[str, Any]:
    """Repair model formatting drift without inventing source provenance.

    Structured-generation models frequently paraphrase the quoted evidence or
    mistype the source/chunk ids they copied out of the prompt. Aligning each
    unambiguous reference to the server-owned chunk pair and replacing the
    model quote with an exact server-owned excerpt before validation runs
    keeps minor drift from failing the whole batch. Unknown or ambiguous
    references are left untouched and still fail validation.
    """

    normalized_chunks = coerce_grounding_chunks(chunks)
    repaired = deepcopy(dict(payload))
    items = repaired.get("items")
    if not isinstance(items, list):
        return repaired
    for item in items:
        if not isinstance(item, dict):
            continue
        references = item.get("references")
        if not isinstance(references, list):
            continue
        for reference in references:
            if not isinstance(reference, dict):
                continue
            chunk = _matching_chunk(reference, item, normalized_chunks)
            if chunk is None:
                continue
            reference["source_id"] = chunk.source_id
            reference["chunk_id"] = chunk.chunk_id
            reference["text_snippet"] = _reference_excerpt(
                chunk.text, str(reference.get("text_snippet") or "")
            )
    return repaired


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
    "is_source_metadata_assessment",
    "parse_json_object",
    "repair_payload_references",
    "validate_source_references",
]
