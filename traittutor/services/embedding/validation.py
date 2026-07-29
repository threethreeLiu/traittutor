"""Validation helpers for embedding vectors."""

from __future__ import annotations

from collections.abc import Sequence
import math
from numbers import Real
from typing import Any


def _context(
    *,
    binding: str | None,
    model: str | None,
    batch_index: int | None,
    total_batches: int | None,
) -> str:
    parts: list[str] = []
    if binding:
        parts.append(f"binding={binding}")
    if model:
        parts.append(f"model={model}")
    if batch_index is not None and total_batches is not None:
        parts.append(f"batch={batch_index}/{total_batches}")
    return f" ({', '.join(parts)})" if parts else ""


def _raise_invalid_vector(message: str, *, item_index: int, context: str) -> None:
    raise ValueError(
        "Embedding provider returned invalid vector "
        f"at item {item_index}{context}: {message}. "
        "RAG requires dense numeric embeddings; check the embedding provider/model "
        "and re-index the knowledge base after fixing it."
    )


def validate_embedding_batch(
    embeddings: Any,
    *,
    expected_count: int,
    binding: str | None = None,
    model: str | None = None,
    batch_index: int | None = None,
    total_batches: int | None = None,
    start_index: int = 0,
) -> list[list[float]]:
    """Return normalized float vectors or raise a clear provider error.

    Provider smoke tests and RAG indexing both ultimately need a list of dense
    numeric vectors. A single ``None`` coordinate otherwise reaches LlamaIndex's
    similarity code and fails later as ``NoneType * float``.
    """

    context = _context(
        binding=binding,
        model=model,
        batch_index=batch_index,
        total_batches=total_batches,
    )

    if (
        embeddings is None
        or isinstance(embeddings, (str, bytes))
        or not isinstance(embeddings, Sequence)
    ):
        raise ValueError(
            "Embedding provider returned invalid embeddings payload"
            f"{context}: expected a list of {expected_count} vector(s), "
            f"got {type(embeddings).__name__}."
        )

    actual_count = len(embeddings)
    if actual_count != expected_count:
        raise ValueError(
            "Embedding provider returned an unexpected number of vectors"
            f"{context}: expected {expected_count}, got {actual_count}. "
            "This usually means the provider dropped one or more inputs; "
            "RAG indexing/search cannot safely continue."
        )

    normalized: list[list[float]] = []
    for local_index, vector in enumerate(embeddings):
        item_index = start_index + local_index
        if vector is None:
            _raise_invalid_vector("vector is null", item_index=item_index, context=context)
        if isinstance(vector, (str, bytes)) or not isinstance(vector, Sequence):
            _raise_invalid_vector(
                f"expected a numeric sequence, got {type(vector).__name__}",
                item_index=item_index,
                context=context,
            )
        if len(vector) == 0:
            _raise_invalid_vector("vector is empty", item_index=item_index, context=context)

        normalized_vector: list[float] = []
        for dim_index, value in enumerate(vector):
            if value is None:
                _raise_invalid_vector(
                    f"dimension {dim_index} is null",
                    item_index=item_index,
                    context=context,
                )
            if isinstance(value, bool) or not isinstance(value, Real):
                _raise_invalid_vector(
                    f"dimension {dim_index} is {type(value).__name__}, not a number",
                    item_index=item_index,
                    context=context,
                )
            numeric = float(value)
            if not math.isfinite(numeric):
                _raise_invalid_vector(
                    f"dimension {dim_index} is not finite",
                    item_index=item_index,
                    context=context,
                )
            normalized_vector.append(numeric)

        normalized.append(normalized_vector)

    dims = {len(vector) for vector in normalized}
    if len(dims) > 1:
        raise ValueError(
            "Embedding provider returned inconsistent vector dimensions"
            f"{context}: dimensions={sorted(dims)}. "
            "Use a single embedding model/dimension and re-index the knowledge base."
        )

    return normalized
