"""F-02/F-11 phase 1: provenance-gated memory activation (invariant #7)."""

from __future__ import annotations

from threading import Barrier, Thread

import pytest

from traittutor.memory import (
    ACTIVATION_EVIDENCE_THRESHOLD,
    MemoryActivationError,
    MemoryAuthorizationError,
    MemoryStore,
)

CREATED = "2026-08-09T08:00:00+00:00"


@pytest.fixture
def store(tmp_path) -> MemoryStore:
    return MemoryStore("user-a", path=tmp_path / "memory.json")


def test_inferred_candidate_cannot_auto_activate(store: MemoryStore) -> None:
    cand = store.propose_candidate(
        scope="subject",
        subject_id="math",
        key="preferred_name",
        value="Alex",
        provenance="inferred",
        confidence=0.6,
        created_at=CREATED,
    )
    # One observation and no user confirmation -> must stay a candidate.
    with pytest.raises(MemoryActivationError):
        store.activate_candidate(cand.candidate_id, evidence_count=1)


def test_inferred_candidate_activates_with_repeated_evidence(store: MemoryStore) -> None:
    cand = store.propose_candidate(
        scope="subject",
        subject_id="math",
        key="preferred_name",
        value="Alex",
        provenance="inferred",
        confidence=0.7,
        created_at=CREATED,
    )
    item = store.activate_candidate(cand.candidate_id, evidence_count=ACTIVATION_EVIDENCE_THRESHOLD)
    assert item.status == "active"
    assert item.provenance == "inferred"


def test_inferred_candidate_activates_with_user_confirmation(store: MemoryStore) -> None:
    cand = store.propose_candidate(
        scope="subject",
        subject_id="math",
        key="tz",
        value="UTC+8",
        provenance="inferred",
        confidence=0.5,
        created_at=CREATED,
    )
    item = store.activate_candidate(cand.candidate_id, confirmed=True)
    assert item.status == "active"
    assert store.candidate(cand.candidate_id).status == "activated"


def test_candidate_conflict_is_displayed_and_can_be_rejected(store: MemoryStore) -> None:
    active = store.add_explicit(scope="subject", subject_id="math", key="exam_date", value="Monday")
    candidate = store.propose_candidate(
        scope="subject",
        subject_id="math",
        key="exam_date",
        value="Tuesday",
        provenance="inferred",
        confidence=0.7,
        evidence_refs=("turn:2",),
    )

    conflict = store.conflicts(scope="subject", subject_id="math")[0]
    assert candidate.status == "conflict"
    assert conflict.candidate_id == candidate.candidate_id
    assert conflict.memory_ids == (active.memory_id,)
    assert conflict.values == ("Monday",)
    assert store.reject_candidate(candidate.candidate_id, source="user:settings").status == (
        "rejected"
    )
    assert store.conflicts(scope="subject", subject_id="math") == []


def test_explicit_activates_directly(store: MemoryStore) -> None:
    item = store.add_explicit(scope="global", key="language", value="en")
    assert item.status == "active"
    assert store.get_active("global", "language") is not None


def test_research_memory_requires_source_ref(store: MemoryStore) -> None:
    with pytest.raises(MemoryActivationError):
        store.add_explicit(scope="research", scope_id="research-1", key="claim", value="X is true")
    cand = store.propose_candidate(
        scope="research",
        scope_id="research-1",
        key="claim",
        value="X is true",
        provenance="inferred",
        confidence=0.8,
        created_at=CREATED,
    )
    with pytest.raises(MemoryActivationError):
        store.activate_candidate(cand.candidate_id, confirmed=True)
    with pytest.raises(MemoryActivationError):
        store.add_explicit(
            scope="research",
            scope_id="research-1",
            key="claim",
            value="X is true",
            source_ref="not-a-clickable-source",
        )


def test_supersede_links_chain_and_marks_old_superseded(store: MemoryStore) -> None:
    first = store.add_explicit(
        scope="subject", subject_id="math", key="goal", value="learn algebra"
    )
    second = store.add_explicit(
        scope="subject", subject_id="math", key="goal", value="learn calculus"
    )
    # Re-fetch the prior item: items are frozen, so supersession writes a new
    # record rather than mutating the returned reference.
    first_now = next(
        item
        for item in store.history("subject", "goal", subject_id="math")
        if item.memory_id == first.memory_id
    )
    assert first_now.status == "superseded"
    assert second.status == "active"
    assert second.supersedes_id == first.memory_id
    assert store.get_active("subject", "goal", subject_id="math").memory_id == second.memory_id
    chain = store.history("subject", "goal", subject_id="math")
    assert [item.memory_id for item in chain] == [first.memory_id, second.memory_id]


def test_concurrent_activations_form_one_supersede_chain(store: MemoryStore) -> None:
    barrier = Barrier(16)

    def activate(index: int) -> None:
        barrier.wait()
        store.add_explicit(scope="subject", subject_id="math", key="goal", value=f"goal-{index}")

    threads = [Thread(target=activate, args=(index,)) for index in range(barrier.parties)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    chain = store.history("subject", "goal", subject_id="math")
    active = [item for item in chain if item.status == "active"]
    assert len(chain) == barrier.parties
    assert len(active) == 1
    assert sum(item.supersedes_id is None for item in chain) == 1
    assert {item.supersedes_id for item in chain if item.supersedes_id} == {
        item.memory_id for item in chain if item.memory_id != active[0].memory_id
    }


def test_delete_removes_from_active_and_search(store: MemoryStore) -> None:
    item = store.add_explicit(scope="global", key="note", value="temporary")
    store.delete(item.memory_id)
    assert store.get_active("global", "note") is None
    assert store.search(scope="global", keyword="temporary") == []


def test_search_filters_by_scope_and_keyword(store: MemoryStore) -> None:
    store.add_explicit(scope="global", key="note", value="calculus basics")
    store.add_explicit(scope="subject", subject_id="math", key="note", value="calculus advanced")
    assert len(store.search(keyword="calculus")) == 2
    assert len(store.search(scope="subject", subject_id="math", keyword="calculus")) == 1


def test_cross_session_owner_and_subject_isolation(tmp_path) -> None:
    path = tmp_path / "memory.json"
    writer = MemoryStore("user-a", path=path)
    item = writer.add_explicit(
        scope="subject", subject_id="math", key="goal", value="learn calculus"
    )
    assert MemoryStore("user-a", path=path).get_active("subject", "goal", subject_id="math") == item
    assert MemoryStore("user-b", path=path).get_active("subject", "goal", subject_id="math") is None
    assert writer.search(scope="subject", subject_id="physics") == []


def test_cross_scope_read_requires_grant_and_is_visible_to_why_drawer(store) -> None:
    item = store.add_explicit(scope="global", key="language", value="zh")
    with pytest.raises(MemoryAuthorizationError):
        store.search(scope="global", requesting_scope="subject")

    results = store.search(
        scope="global",
        requesting_scope="subject",
        cross_scope_authorized=True,
        snapshot_id="snapshot-1",
        purpose="assemble next prompt",
    )
    assert results == [item]
    records = store.list_access_records("snapshot-1")
    assert len(records) == 1
    assert records[0].version_read == item.memory_id
    assert records[0].user_authorized is True


def test_same_scope_other_subject_also_requires_explicit_grant(store) -> None:
    store.add_explicit(scope="subject", subject_id="physics", key="goal", value="learn mechanics")
    with pytest.raises(MemoryAuthorizationError):
        store.search(
            scope="subject",
            subject_id="physics",
            requesting_scope="subject",
            requesting_subject_id="math",
        )


def test_subject_memory_isolated_by_kc_and_cross_kc_requires_grant(store) -> None:
    derivative = store.add_explicit(
        scope="subject",
        subject_id="math",
        kc_id="derivative",
        key="support_note",
        value="use a graph",
    )
    store.add_explicit(
        scope="subject",
        subject_id="math",
        kc_id="integral",
        key="support_note",
        value="use area examples",
    )
    assert store.search(scope="subject", subject_id="math", kc_id="derivative") == [derivative]
    with pytest.raises(MemoryAuthorizationError):
        store.search(
            scope="subject",
            subject_id="math",
            kc_id="integral",
            requesting_scope="subject",
            requesting_subject_id="math",
            requesting_kc_id="derivative",
        )


def test_every_snapshot_read_is_available_to_why_drawer(store) -> None:
    item = store.add_explicit(
        scope="subject", subject_id="math", key="goal", value="learn calculus"
    )
    assert store.search(
        scope="subject",
        subject_id="math",
        requesting_scope="subject",
        requesting_subject_id="math",
        snapshot_id="snapshot-same-domain",
    ) == [item]
    records = store.list_access_records("snapshot-same-domain")
    assert len(records) == 1
    assert records[0].key == item.memory_id


def test_lifecycle_provenance_and_delete_cascade_remove_fresh_prompt_values(store) -> None:
    first = store.add_explicit(
        scope="subject", subject_id="math", key="goal", value="algebra", source="turn:1"
    )
    second = store.add_explicit(
        scope="subject", subject_id="math", key="goal", value="calculus", source="turn:2"
    )
    store.delete(first.memory_id, source="settings:forget")

    assert store.search(scope="subject", subject_id="math", keyword="algebra") == []
    assert store.search(scope="subject", subject_id="math", keyword="calculus") == []
    assert {record.action for record in store.lifecycle()} >= {
        "activate",
        "supersede",
        "delete",
    }
    assert store.lifecycle(second.memory_id)[-1].source == "settings:forget"


@pytest.mark.parametrize(
    "long_memory_class",
    [
        "single_session_extraction",
        "cross_session_reasoning",
        "temporal_reasoning",
        "information_update",
        "abstention",
    ],
)
def test_longmemeval_class_placeholder(long_memory_class: str) -> None:
    """Reserve the five LongMemEval classes for production fixture wiring."""
    assert long_memory_class
