"""Canonical voids preserve audit facts while retracting effective evidence."""

from __future__ import annotations

import pytest

from traittutor.learning_governance.repository import build_subject_learning_state_snapshot
from traittutor.learning_model import (
    KnowledgeStateKey,
    LearnerEvent,
    LearnerEventAmendment,
    LearnerEventLedger,
    rebuild_knowledge_states,
    stable_amendment_identity,
)

NOW = "2026-08-10T08:00:00+00:00"
LATER = "2026-08-10T09:00:00+00:00"


def _event(
    event_id: str,
    *,
    correct: bool | None,
    strength: str = "strong",
    user_id: str = "learner-a",
    subject_id: str = "math",
    kc_id: str = "fractions",
    created_at: str = NOW,
) -> LearnerEvent:
    return LearnerEvent(
        event_id=event_id,
        idempotency_key=f"submit:{event_id}",
        user_id=user_id,
        subject_id=subject_id,
        kc_ids=(kc_id,),
        surface_type="quiz",
        answer_correct=correct,
        evidence_strength=strength,  # type: ignore[arg-type]
        attribution_status="reliable",
        created_at=created_at,
    )


def _void(ledger: LearnerEventLedger, event: LearnerEvent) -> LearnerEventAmendment:
    amendment_id, idempotency_key = stable_amendment_identity(
        user_id=event.user_id,
        target_event_id=event.event_id,
    )
    amendment = LearnerEventAmendment(
        amendment_id=amendment_id,
        idempotency_key=idempotency_key,
        target_event_id=event.event_id,
        user_id=event.user_id,
        subject_id=event.subject_id,
        kc_ids=event.kc_ids,
        reason_code="item_invalid",
        created_at=LATER,
    )
    assert ledger.append_amendment(amendment) == "appended"
    return amendment


def test_void_is_idempotent_and_rebuild_replays_only_effective_evidence(tmp_path) -> None:
    ledger = LearnerEventLedger(tmp_path / "events.json")
    wrong = _event("wrong", correct=False)
    correct = _event("correct", correct=True, created_at=LATER)
    assert ledger.append(wrong) == "appended"
    assert ledger.append(correct) == "appended"

    before = rebuild_knowledge_states(ledger.effective_events())
    before_state = before.get_or_seed(
        KnowledgeStateKey(user_id="learner-a", subject_id="math", kc_id="fractions"), now=NOW
    )
    assert before_state.verified_observation_count == 2

    amendment = _void(ledger, wrong)
    assert ledger.append_amendment(amendment) == "duplicate"
    assert [event.event_id for event in ledger] == ["wrong", "correct"]
    assert [event.event_id for event in ledger.effective_events()] == ["correct"]

    replayed: list[str] = []
    assert ledger.replay(lambda event: replayed.append(event.event_id)) == 1
    assert replayed == ["correct"]
    snapshot = build_subject_learning_state_snapshot(
        owner_id="learner-a", subject_id="math", event_ledger=ledger
    )
    assert snapshot.strong_event_count == 1
    assert snapshot.knowledge[0].verified_observation_count == 1

    restored = LearnerEventLedger(tmp_path / "events.json")
    assert restored.amendment_for_target("wrong") == amendment
    assert [event.event_id for event in restored.strong_evidence_for(user_id="learner-a")] == [
        "correct"
    ]


def test_void_rejects_cross_owner_subject_or_kc_target() -> None:
    ledger = LearnerEventLedger()
    event = _event("event-a", correct=False)
    ledger.append(event)
    amendment_id, key = stable_amendment_identity(
        user_id="learner-b", target_event_id=event.event_id
    )
    cross_owner = LearnerEventAmendment(
        amendment_id=amendment_id,
        idempotency_key=key,
        target_event_id=event.event_id,
        user_id="learner-b",
        subject_id="math",
        kc_ids=("fractions",),
        reason_code="grading_error",
        created_at=LATER,
    )
    with pytest.raises(PermissionError):
        ledger.append_amendment(cross_owner)

    amendment_id, key = stable_amendment_identity(
        user_id="learner-a", target_event_id=event.event_id
    )
    wrong_partition = LearnerEventAmendment(
        amendment_id=amendment_id,
        idempotency_key=key,
        target_event_id=event.event_id,
        user_id="learner-a",
        subject_id="science",
        kc_ids=("fractions",),
        reason_code="attribution_error",
        created_at=LATER,
    )
    with pytest.raises(PermissionError):
        ledger.append_amendment(wrong_partition)
    assert ledger.is_effective(event.event_id)


def test_weak_evidence_remains_non_bkt_before_and_after_void() -> None:
    ledger = LearnerEventLedger()
    weak = _event("weak", correct=None, strength="exposure")
    ledger.append(weak)
    assert ledger.strong_evidence_for(user_id="learner-a") == []
    revision_before = build_subject_learning_state_snapshot(
        owner_id="learner-a", subject_id="math", event_ledger=ledger
    ).source_revision
    _void(ledger, weak)
    snapshot = build_subject_learning_state_snapshot(
        owner_id="learner-a", subject_id="math", event_ledger=ledger
    )
    assert snapshot.strong_event_count == 0
    assert snapshot.knowledge == ()
    assert snapshot.source_revision != revision_before


def test_void_reconciliation_is_a_durable_token_fenced_outbox(tmp_path) -> None:
    ledger = LearnerEventLedger(tmp_path / "events.json")
    event = _event("reconcile", correct=False)
    ledger.append(event)
    amendment_id, key = stable_amendment_identity(
        user_id=event.user_id, target_event_id=event.event_id
    )
    amendment = LearnerEventAmendment(
        amendment_id=amendment_id,
        idempotency_key=key,
        target_event_id=event.event_id,
        user_id=event.user_id,
        subject_id=event.subject_id,
        kc_ids=event.kc_ids,
        reason_code="grading_error",
        created_at=LATER,
    )
    operation = ledger.amendment_reconciliation_operation(amendment_id)
    ledger.append_amendment(amendment, reconciliation_operation=operation)
    assert [
        (item.event_id, item.operation)
        for item in LearnerEventLedger(ledger.path).pending_derived()
    ] == [(event.event_id, operation)]
    claim = ledger.claim_derived(event.event_id, operation, now=LATER)
    assert claim is not None
    assert (
        ledger.mark_derived_applied(event.event_id, operation, claim_token=claim.token) == "applied"
    )
    assert LearnerEventLedger(ledger.path).pending_derived() == []
