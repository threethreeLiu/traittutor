"""Bounded deterministic ranking for already-authorized canonical memory."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Mapping, Sequence

from .models import UserMemoryItem

MAX_MEMORY_RESULTS = 12
MAX_MEMORY_TOKEN_BUDGET = 2048
_RRF_K = 60


@dataclass(frozen=True)
class MemoryHybridSearchResult:
    """Request-local hybrid result; query text is intentionally never retained."""

    items: tuple[UserMemoryItem, ...]
    degradation_reasons: tuple[str, ...] = ()
    trimmed_count: int = 0


def _stable_item_key(item: UserMemoryItem) -> tuple[str, ...]:
    return (
        item.scope,
        item.scope_id or "",
        item.subject_id or "",
        item.kc_id or "",
        item.key,
        item.valid_from,
        item.memory_id,
    )


def _lexical_score(item: UserMemoryItem, keyword: str) -> float:
    needle = keyword.casefold().strip()
    if not needle:
        return 0.0
    haystack = f"{item.key} {item.value}".casefold()
    terms = tuple(dict.fromkeys(re.findall(r"\w+", needle, flags=re.UNICODE)))
    matched_terms = sum(term in haystack for term in terms)
    phrase_bonus = 2.0 if needle in haystack else 0.0
    coverage = matched_terms / len(terms) if terms else 0.0
    return phrase_bonus + coverage


def _estimated_tokens(item: UserMemoryItem) -> int:
    # One Unicode code point per token is conservative for the Chinese-first
    # product and deterministic across machines without adding a tokenizer.
    return max(1, len(item.key) + len(item.value) + 2)


def rank_memory_candidates(
    candidates: Sequence[UserMemoryItem],
    *,
    keyword: str | None,
    vector_scores: Mapping[str, float] | None,
    limit: int,
    token_budget: int,
    degradation_reasons: Sequence[str] = (),
) -> MemoryHybridSearchResult:
    """Fuse lexical/vector ranks after authorization, then enforce hard bounds."""
    unique: list[UserMemoryItem] = []
    seen: set[str] = set()
    for item in candidates:
        if item.memory_id in seen:
            continue
        seen.add(item.memory_id)
        unique.append(item)

    normalized_keyword = (keyword or "").strip()
    lexical_scores = {
        item.memory_id: score
        for item in unique
        if (score := _lexical_score(item, normalized_keyword)) > 0
    }
    lexical_order = sorted(
        (item for item in unique if item.memory_id in lexical_scores),
        key=lambda item: (-lexical_scores[item.memory_id], _stable_item_key(item)),
    )
    vector_order = sorted(
        (
            item
            for item in unique
            if vector_scores is not None and vector_scores.get(item.memory_id, 0.0) > 0
        ),
        key=lambda item: (
            -float(vector_scores[item.memory_id]) if vector_scores is not None else 0.0,
            _stable_item_key(item),
        ),
    )

    if normalized_keyword or vector_scores is not None:
        fused: dict[str, float] = {}
        for rank, item in enumerate(lexical_order, start=1):
            fused[item.memory_id] = fused.get(item.memory_id, 0.0) + 1 / (_RRF_K + rank)
        for rank, item in enumerate(vector_order, start=1):
            fused[item.memory_id] = fused.get(item.memory_id, 0.0) + 1 / (_RRF_K + rank)
        ranked = sorted(
            (item for item in unique if item.memory_id in fused),
            key=lambda item: (-fused[item.memory_id], _stable_item_key(item)),
        )
    else:
        # Preserve the caller's precedence for the no-query default path.
        ranked = unique

    effective_limit = max(0, min(limit, MAX_MEMORY_RESULTS))
    effective_budget = max(0, min(token_budget, MAX_MEMORY_TOKEN_BUDGET))
    selected: list[UserMemoryItem] = []
    token_used = 0
    for item in ranked:
        if len(selected) >= effective_limit:
            break
        item_tokens = _estimated_tokens(item)
        if token_used + item_tokens > effective_budget:
            continue
        selected.append(item)
        token_used += item_tokens

    return MemoryHybridSearchResult(
        items=tuple(selected),
        degradation_reasons=tuple(dict.fromkeys(degradation_reasons)),
        trimmed_count=max(0, len(ranked) - len(selected)),
    )


__all__ = [
    "MAX_MEMORY_RESULTS",
    "MAX_MEMORY_TOKEN_BUDGET",
    "MemoryHybridSearchResult",
    "rank_memory_candidates",
]
