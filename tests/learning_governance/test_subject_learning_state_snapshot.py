"""Canonical, read-only subject-state snapshot contracts."""

from __future__ import annotations

from pathlib import Path

import pytest

from traittutor.context_assembler import ContextAssembler
from traittutor.learning.storage import LearningStore
from traittutor.learning_governance.repository import (
    LearningGovernanceRepository,
    OwnerBoundLearningStore,
    build_subject_learning_state_snapshot,
)
from traittutor.learning_model.events import LearnerEvent, LearnerEventLedger
from traittutor.learning_model.misconception import MisconceptionStore
from traittutor.memory.store import MemoryStore
from traittutor.personalization.models import PersonalizationContext, TeachingStrategyPlan

NOW = "2026-08-10T00:00:00+00:00"


def _event(
    event_id: str,
    *,
    owner_id: str = "alice",
    subject_id: str | None = "math",
    kc_id: str = "fractions",
    correct: bool = True,
    strong: bool = True,
) -> LearnerEvent:
    return LearnerEvent(
        event_id=event_id,
        idempotency_key=f"idem-{event_id}",
        user_id=owner_id,
        subject_id=subject_id,
        kc_ids=(kc_id,),
        surface_type="quiz",
        item_id=f"item-{event_id}",
        answer_correct=correct if strong else None,
        evidence_strength="strong" if strong else "none",
        attribution_status="reliable" if strong else "attribution_pending",
        created_at=NOW,
    )


def _repository(tmp_path: Path, ledger: LearnerEventLedger) -> LearningGovernanceRepository:
    return LearningGovernanceRepository(
        owner_id="alice",
        learning_source=OwnerBoundLearningStore(
            owner_id="alice",
            store=LearningStore(tmp_path / "learning"),
        ),
        event_ledger=ledger,
        misconception_store=MisconceptionStore(tmp_path / "misconceptions.json", owner_id="alice"),
    )


def test_subject_snapshot_is_replay_deterministic_and_owner_subject_isolated(
    tmp_path: Path,
) -> None:
    events = (
        _event("alice-math-1", correct=False),
        _event("alice-math-2", kc_id="algebra"),
        _event("alice-physics", subject_id="physics", kc_id="vectors"),
        _event("bob-math", owner_id="bob"),
        _event("weak-or-legacy", strong=False),
        _event("unversioned", subject_id=None),
    )
    first = LearnerEventLedger()
    replayed = LearnerEventLedger()
    for event in events:
        first.append(event)
    for event in reversed(events):
        replayed.append(event)

    snapshot = _repository(tmp_path, first).subject_learning_state_snapshot(subject_id="math")
    replay_snapshot = build_subject_learning_state_snapshot(
        owner_id="alice",
        subject_id="math",
        event_ledger=replayed,
    )
    physics = build_subject_learning_state_snapshot(
        owner_id="alice",
        subject_id="physics",
        event_ledger=first,
    )
    bob = build_subject_learning_state_snapshot(
        owner_id="bob",
        subject_id="math",
        event_ledger=first,
    )

    assert snapshot == replay_snapshot
    assert snapshot.strong_event_count == 2
    assert [(item.kc_id, item.verified_observation_count) for item in snapshot.knowledge] == [
        ("algebra", 1),
        ("fractions", 1),
    ]
    assert all(item.evidence_state == "insufficient_evidence" for item in snapshot.knowledge)
    assert physics.strong_event_count == 1
    assert [item.kc_id for item in physics.knowledge] == ["vectors"]
    assert bob.strong_event_count == 1
    # Neither the pending/unversioned facts nor private grading payloads enter
    # the canonical read-model contract or cause a derived BKT write.
    dumped = snapshot.model_dump(mode="json")
    assert "weak-or-legacy" not in str(dumped)
    assert "unversioned" not in str(dumped)
    assert not {"answer_correct", "rubric", "review"}.intersection(dumped)
    assert len(first) == len(events)


def test_context_uses_only_authoritative_subject_snapshot_reference_and_safe_envelope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = LearnerEventLedger()
    ledger.append(_event("alice-math", correct=False))
    ledger.append(_event("alice-physics", subject_id="physics", kc_id="vectors"))
    math = build_subject_learning_state_snapshot(
        owner_id="alice", subject_id="math", event_ledger=ledger
    )
    physics = build_subject_learning_state_snapshot(
        owner_id="alice", subject_id="physics", event_ledger=ledger
    )
    snapshots = {"math": math, "physics": physics}
    assembler = ContextAssembler(
        canonical_memory_store_factory=lambda owner_id: MemoryStore(
            owner_id, path=tmp_path / "memory.json"
        ),
        subject_learning_state_snapshot_factory=lambda owner_id, subject_id: snapshots[subject_id],
    )
    monkeypatch.setattr(
        assembler,
        "_read_personalization_context",
        lambda **_kwargs: (
            PersonalizationContext(
                purpose="courseware",
                plan=TeachingStrategyPlan(),
                trace_id="personalization-context",
            ),
            "profile-v1",
        ),
    )

    snapshot = assembler.assemble(
        intent="learn",
        user_id="alice",
        subject_id="math",
        token_budget=100,
        trace_id="subject-state-context",
        created_at=NOW,
        user_authorized=True,
        include_tutor_persona=False,
    )

    ref = snapshot.read_ranges.subject_learning_state_ref
    assert ref is not None
    assert ref.owner_id == "alice"
    assert ref.subject_id == "math"
    assert ref.source_revision == math.source_revision
    assert ref.strong_event_count == 1
    assert assembler.personalization_context is not None
    envelope = next(
        value
        for value in assembler.personalization_context.constraints
        if value.startswith("canonical_subject_evidence=")
    )
    assert math.source_revision in envelope
    assert physics.source_revision not in envelope
    assert "answer_correct" not in envelope
    assert "rubric" not in envelope
    assert "fractions" not in envelope
    assert len(ledger) == 2
    audit_records = assembler.access_log.for_snapshot(snapshot.snapshot_id)
    assert any(
        record.scope == "canonical_subject_state"
        and record.key == "alice:math"
        and record.version_read == math.source_revision
        for record in audit_records
    )
