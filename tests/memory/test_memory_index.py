from __future__ import annotations

import json

import pytest

from traittutor.memory.index_projection import (
    build_memory_index,
    validate_source_allowlist,
)
from traittutor.memory.index_store import MemoryIndexStore, StaleMemoryIndexGenerationError
from traittutor.memory.store import MemoryStore
from traittutor.unified_storage import SectionedRecordStore


def test_index_keeps_exact_canonical_provenance(tmp_path) -> None:
    memory = MemoryStore("alice", path=tmp_path / "memory.json").add_explicit(
        scope="subject",
        subject_id="math",
        key="example_style",
        value="visual",
        evidence_refs=("turn:7",),
        source_ref="https://example.test/source",
    )
    index = build_memory_index(owner_id="alice", entry_id="profile", generation=1, items=(memory,))

    claim = index.claims[0]
    assert claim.source_entry_ids == (memory.memory_id,)
    assert claim.source_refs == ("turn:7", "https://example.test/source")
    assert claim.observed_from == memory.valid_from
    assert claim.subject_id == "math"
    assert claim.confidence == 1.0
    assert claim.assertion_state == "verified"
    validate_source_allowlist(index, {memory.memory_id})
    with pytest.raises(ValueError, match="unknown source ids"):
        validate_source_allowlist(index, set())


def test_late_rebuild_cannot_overwrite_current_generation(tmp_path) -> None:
    memory_store = MemoryStore("alice", path=tmp_path / "memory.json")
    item = memory_store.add_explicit(scope="global", key="language", value="zh")
    index_store = MemoryIndexStore("alice", path=tmp_path / "index.json")
    stale = index_store.begin_rebuild()
    stale_index = build_memory_index(
        owner_id="alice", entry_id="profile", generation=stale.generation, items=(item,)
    )
    current = index_store.begin_rebuild()
    current_index = build_memory_index(
        owner_id="alice", entry_id="profile", generation=current.generation, items=(item,)
    )
    index_store.commit_rebuild(current, (current_index,), allowed_memory_ids={item.memory_id})

    with pytest.raises(StaleMemoryIndexGenerationError):
        index_store.commit_rebuild(stale, (stale_index,), allowed_memory_ids={item.memory_id})
    assert index_store.list_indexes() == [current_index]


def test_delete_fences_started_rebuild_and_removes_index(tmp_path) -> None:
    memory_store = MemoryStore("alice", path=tmp_path / "memory.json")
    item = memory_store.add_explicit(scope="global", key="note", value="private")
    index_store = MemoryIndexStore("alice", path=tmp_path / "index.json")
    initial = index_store.begin_rebuild()
    index = build_memory_index(
        owner_id="alice", entry_id="profile", generation=initial.generation, items=(item,)
    )
    index_store.commit_rebuild(initial, (index,), allowed_memory_ids={item.memory_id})
    late = index_store.begin_rebuild()
    late_index = build_memory_index(
        owner_id="alice", entry_id="profile", generation=late.generation, items=(item,)
    )

    memory_store.delete(item.memory_id)
    index_store.invalidate_memory(item.memory_id)

    assert index_store.list_indexes() == []
    with pytest.raises(StaleMemoryIndexGenerationError):
        index_store.commit_rebuild(late, (late_index,), allowed_memory_ids=set())


def test_legacy_text_projection_is_retired_without_becoming_memory(tmp_path) -> None:
    path = tmp_path / "index.json"
    # Seed the legacy shape directly into the unified DB (a legacy_unverified
    # index cannot enter via the current build_memory_index API by design — it
    # only ever existed in pre-v2 files, now migrated).  The store then retires
    # it exactly as it would for migrated historical data.
    seeder = SectionedRecordStore("memory_index", "alice", schema_version=2, legacy_path=path)
    seeder.replace_all(
        {
            "schema_version": 2,
            "states": [{"owner_id": "alice", "generation": 2, "active_token": None}],
            "indexes": [
                {
                    "owner_id": "alice",
                    "claims": [{"assertion_state": "legacy_unverified"}],
                    "markdown": "old private text",
                }
            ],
            "invalidations": [],
        }
    )
    store = MemoryIndexStore("alice", path=path)

    assert store.retire_legacy_indexes() == 1
    assert store.current_generation() == 3
    assert store.list_indexes() == []
    # The legacy projection text must not survive the retirement round-trip.
    assert all("old private text" not in json.dumps(index) for index in store._load()["indexes"])


def test_hybrid_search_filters_scope_before_embedding_and_uses_vector_query(tmp_path) -> None:
    memory_path = tmp_path / "memory.json"
    index_path = tmp_path / "index.json"
    canonical = MemoryStore("alice", path=memory_path)
    semantic = canonical.add_explicit(
        scope="subject",
        subject_id="math",
        key="example_style",
        value="visual diagrams",
    )
    lexical = canonical.add_explicit(
        scope="subject",
        subject_id="math",
        key="timeline_note",
        value="timeline practice",
    )
    forbidden = canonical.add_explicit(
        scope="subject",
        subject_id="history",
        key="private_history",
        value="forbidden archive",
    )
    index_store = MemoryIndexStore("alice", path=index_path)
    token = index_store.begin_rebuild()
    index = build_memory_index(
        owner_id="alice",
        entry_id="profile",
        generation=token.generation,
        items=(semantic, lexical, forbidden),
    )
    index_store.commit_rebuild(
        token,
        (index,),
        allowed_memory_ids={semantic.memory_id, lexical.memory_id, forbidden.memory_id},
    )
    embedded_texts: list[str] = []

    def embed_claims(texts: list[str]) -> list[list[float]]:
        embedded_texts.extend(texts)
        return [[1.0, 0.0] if "visual diagrams" in text else [0.0, 1.0] for text in texts]

    retrieval = MemoryStore(
        "alice",
        path=memory_path,
        index_store=index_store,
        embedding_batch=embed_claims,
    )
    result = retrieval.search_hybrid(
        scope="subject",
        subject_id="math",
        keyword="timeline",
        vector_query=(1.0, 0.0),
        limit=2,
        token_budget=200,
    )

    assert {item.memory_id for item in result.items} == {
        semantic.memory_id,
        lexical.memory_id,
    }
    assert forbidden.memory_id not in {item.memory_id for item in result.items}
    assert all("forbidden archive" not in text for text in embedded_texts)
    assert result.degradation_reasons == ()


def test_vector_failure_degrades_to_bounded_lexical_without_saving_query(tmp_path) -> None:
    memory_path = tmp_path / "memory.json"
    index_path = tmp_path / "index.json"
    canonical = MemoryStore("alice", path=memory_path)
    lexical = canonical.add_explicit(
        scope="global",
        key="pacing",
        value="stepwise pacing",
    )
    extra = canonical.add_explicit(scope="global", key="language", value="Chinese")
    index_store = MemoryIndexStore("alice", path=index_path)
    token = index_store.begin_rebuild()
    index = build_memory_index(
        owner_id="alice",
        entry_id="profile",
        generation=token.generation,
        items=(lexical, extra),
    )
    index_store.commit_rebuild(
        token,
        (index,),
        allowed_memory_ids={lexical.memory_id, extra.memory_id},
    )

    def unavailable(_texts: list[str]) -> list[list[float]]:
        raise RuntimeError("vector backend unavailable")

    retrieval = MemoryStore(
        "alice",
        path=memory_path,
        index_store=index_store,
        embedding_batch=unavailable,
    )
    result = retrieval.search_hybrid(
        scope="global",
        keyword="stepwise private-query-marker",
        vector_query=(1.0, 0.0),
        limit=1,
        token_budget=100,
    )

    assert result.items == (lexical,)
    assert result.degradation_reasons == ("canonical_memory_vector_failed",)
    assert result.trimmed_count == 0
    # The request-local query marker must never be persisted into the index DB.
    assert "private-query-marker" not in json.dumps(index_store._load())

    bounded = retrieval.rank_candidates(
        (lexical, extra, lexical),
        keyword=None,
        limit=1,
        token_budget=100,
    )
    too_small = retrieval.rank_candidates(
        (lexical, extra),
        keyword=None,
        limit=12,
        token_budget=1,
    )
    assert bounded.items == (lexical,)
    assert bounded.trimmed_count == 1
    assert too_small.items == ()
    assert too_small.trimmed_count == 2
