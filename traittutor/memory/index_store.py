"""Owner-bound durable storage and generation fencing for memory indexes."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
import math
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from traittutor.services.path_service import PathService
from traittutor.unified_storage import SectionedRecordStore

from .index_projection import MemoryIndex, validate_source_allowlist
from .models import _require_utc_iso


def _now() -> str:
    return datetime.now(UTC).isoformat()


class StaleMemoryIndexGenerationError(RuntimeError):
    """A late rebuild or rollback attempted to overwrite newer state."""


class MemoryIndexRebuildToken(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    owner_id: str
    generation: int = Field(ge=1)
    token: str
    created_at: str

    _created_at = field_validator("created_at")(_require_utc_iso)


class MemoryIndexStore:
    """Persist indexes via the owner-bound unified database.

    The legacy JSON file (``memory-index-v1.json``) is no longer the source of
    truth after the Phase 5 cut-over: reads and writes go through the
    :class:`SectionedRecordStore` adapter, which reconstructs the same
    dict-of-sections payload shape (``states`` / ``indexes`` /
    ``invalidations``) so the generation fencing, fail-closed validation and
    cross-scope read logic above are unchanged.
    """

    def __init__(
        self,
        owner_id: str,
        *,
        path: Path | None = None,
        path_service: PathService | None = None,
        db_path: Any | None = None,
    ) -> None:
        if not owner_id.strip():
            raise ValueError("owner_id is required")
        self.owner_id = owner_id
        # ``path`` is retained only to keep legacy ``path=`` constructors
        # working (tests, transitional callers); it selects an isolated DB
        # location via the adapter and is never read or written as a file.
        self._store_path = path
        self._adapter = SectionedRecordStore(
            "memory_index",
            owner_id,
            schema_version=2,
            path_service=path_service,
            db_path=db_path,
            legacy_path=path,
        )

    def _load(self) -> dict[str, Any]:
        """Read the current index payload (states/indexes/invalidations)."""
        return self._adapter.snapshot()

    def _save(self, payload: dict[str, Any]) -> None:
        """Replace the full index payload inside the active transaction."""
        self._adapter.replace_all(payload)

    @contextmanager
    def _locked(self) -> Iterator[dict[str, Any]]:
        """Exclusive load + mutate block; one immediate DB transaction."""
        with self._adapter.locked() as payload:
            yield payload

    def _state(self, payload: dict[str, Any]) -> dict[str, Any]:
        state = next(
            (row for row in payload["states"] if row.get("owner_id") == self.owner_id),
            None,
        )
        if state is None:
            state = {"owner_id": self.owner_id, "generation": 0, "active_token": None}
            payload["states"].append(state)
        return state

    def current_generation(self) -> int:
        payload = self._load()
        state = next(
            (row for row in payload["states"] if row.get("owner_id") == self.owner_id),
            None,
        )
        return int(state.get("generation", 0)) if state is not None else 0

    def begin_rebuild(self) -> MemoryIndexRebuildToken:
        with self._locked() as payload:
            state = self._state(payload)
            state["generation"] = int(state["generation"]) + 1
            state["active_token"] = f"memory-index-token:{uuid4().hex}"
            token = MemoryIndexRebuildToken(
                owner_id=self.owner_id,
                generation=state["generation"],
                token=state["active_token"],
                created_at=_now(),
            )
            self._save(payload)
            return token

    def commit_rebuild(
        self,
        token: MemoryIndexRebuildToken,
        indexes: tuple[MemoryIndex, ...],
        *,
        allowed_memory_ids: set[str],
    ) -> tuple[MemoryIndex, ...]:
        """Replace this owner's index set if the rebuild still owns the generation."""
        with self._locked() as payload:
            state = self._state(payload)
            if (
                token.owner_id != self.owner_id
                or token.generation != state["generation"]
                or token.token != state["active_token"]
            ):
                raise StaleMemoryIndexGenerationError("Memory index rebuild generation is stale")
            for index in indexes:
                if index.owner_id != self.owner_id:
                    raise PermissionError("index belongs to another owner")
                if index.generation != token.generation:
                    raise ValueError("index generation does not match rebuild token")
                validate_source_allowlist(index, allowed_memory_ids)
            payload["indexes"] = [
                row for row in payload["indexes"] if row.get("owner_id") != self.owner_id
            ] + [index.model_dump(mode="json") for index in indexes]
            state["active_token"] = None
            self._save(payload)
            return indexes

    def invalidate_memory(self, memory_id: str, *, operation_id: str | None = None) -> int:
        """Fence in-flight builds and remove every display index using a deleted fact."""
        with self._locked() as payload:
            if operation_id is not None:
                replay = next(
                    (
                        row
                        for row in payload["invalidations"]
                        if row.get("owner_id") == self.owner_id
                        and row.get("operation_id") == operation_id
                    ),
                    None,
                )
                if replay is not None:
                    if replay.get("memory_id") != memory_id:
                        raise ValueError("invalidation operation_id was reused for another memory")
                    return int(replay["generation"])
            state = self._state(payload)
            state["generation"] = int(state["generation"]) + 1
            state["active_token"] = None
            payload["indexes"] = [
                row
                for row in payload["indexes"]
                if row.get("owner_id") != self.owner_id
                or all(
                    memory_id not in claim.get("source_entry_ids", [])
                    for claim in row.get("claims", [])
                )
            ]
            if operation_id is not None:
                payload["invalidations"].append(
                    {
                        "owner_id": self.owner_id,
                        "operation_id": operation_id,
                        "memory_id": memory_id,
                        "generation": int(state["generation"]),
                        "created_at": _now(),
                    }
                )
            self._save(payload)
            return int(state["generation"])

    def list_indexes(self) -> list[MemoryIndex]:
        return [
            MemoryIndex.model_validate(row)
            for row in self._load()["indexes"]
            if row.get("owner_id") == self.owner_id
        ]

    def vector_scores(
        self,
        vector_query: tuple[float, ...],
        *,
        allowed_memory_ids: set[str],
        embed_claims: Callable[[list[str]], Sequence[Sequence[float]]],
    ) -> dict[str, float]:
        """Score only current-generation claims whose sources are authorized.

        The query vector is request-local and is never added to the persisted
        index or access log. Filtering claims before the embedding call keeps
        unauthorized text outside both ranking and the embedding boundary.
        """
        if not vector_query or any(not math.isfinite(value) for value in vector_query):
            raise ValueError("vector_query must contain finite values")
        payload = self._load()
        state = next(
            (row for row in payload["states"] if row.get("owner_id") == self.owner_id),
            None,
        )
        generation = int(state.get("generation", 0)) if state is not None else 0
        claims = []
        for row in payload["indexes"]:
            if row.get("owner_id") != self.owner_id or int(row.get("generation", -1)) != generation:
                continue
            index = MemoryIndex.model_validate(row)
            for claim in index.claims:
                source_ids = set(claim.source_entry_ids)
                if source_ids and source_ids.issubset(allowed_memory_ids):
                    claims.append(claim)
        claims.sort(key=lambda claim: (claim.claim_id, claim.source_entry_ids))
        if not claims:
            return {}

        embeddings = list(embed_claims([claim.text for claim in claims]))
        if len(embeddings) != len(claims):
            raise ValueError("embedding provider returned an unexpected claim count")
        query_norm = math.sqrt(sum(value * value for value in vector_query))
        if query_norm == 0:
            raise ValueError("vector_query must have non-zero magnitude")

        scores: dict[str, float] = {}
        for claim, embedding_values in zip(claims, embeddings, strict=True):
            embedding = tuple(float(value) for value in embedding_values)
            if len(embedding) != len(vector_query) or any(
                not math.isfinite(value) for value in embedding
            ):
                raise ValueError("claim embedding is incompatible with vector_query")
            embedding_norm = math.sqrt(sum(value * value for value in embedding))
            if embedding_norm == 0:
                continue
            score = sum(
                query_value * claim_value
                for query_value, claim_value in zip(vector_query, embedding, strict=True)
            ) / (query_norm * embedding_norm)
            for memory_id in claim.source_entry_ids:
                scores[memory_id] = max(scores.get(memory_id, -1.0), score)
        return scores

    def retire_legacy_indexes(self) -> int:
        """Remove obsolete text-only indexes after one-way candidate migration."""
        with self._locked() as payload:
            retained = [
                row
                for row in payload["indexes"]
                if row.get("owner_id") != self.owner_id
                or not any(
                    claim.get("assertion_state") == "legacy_unverified"
                    for claim in row.get("claims", [])
                )
            ]
            removed = len(payload["indexes"]) - len(retained)
            if not removed:
                return 0
            state = self._state(payload)
            state["generation"] = int(state["generation"]) + 1
            state["active_token"] = None
            payload["indexes"] = retained
            payload.pop("checkpoints", None)
            self._save(payload)
            return removed


__all__ = [
    "MemoryIndexStore",
    "MemoryIndexRebuildToken",
    "StaleMemoryIndexGenerationError",
]
