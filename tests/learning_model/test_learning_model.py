"""WS-10 phase 1: canonical learning-model objects (F-09/F-10).

Covers the gates the v2.7 plan hangs on: ledger idempotency (#4), strong-
evidence-only BKT (#2), user+subject+kc isolation (#6), uncalibrated display
(WS-10 acceptance), and the "one error != misconception" rule.
"""

from __future__ import annotations

from multiprocessing import get_context
from pathlib import Path
from threading import Barrier, Thread
from typing import Any

import pytest

from traittutor.learning_model import (
    MIN_OBSERVATIONS_FOR_PROBABILITY,
    MISCONCEPTION_EVIDENCE_THRESHOLD,
    BKTParamSet,
    KnowledgeStateKey,
    KnowledgeStateStore,
    LearnerEvent,
    LearnerEventLedger,
    MisconceptionConfirmationError,
    MisconceptionStore,
    display_mastery,
    is_strong_evidence,
    rebuild_knowledge_states,
    update_with_evidence,
)
from traittutor.multi_user.models import LOCAL_ADMIN_ID
from traittutor.personalization.bkt_math import bkt_update
from traittutor.unified_storage import SectionedRecordStore

NOW = "2026-08-09T08:00:00+00:00"
LATER = "2026-08-09T09:00:00+00:00"


def _claim_in_process(path: str, start: Any, results: Any) -> None:
    """Spawn-safe worker proving the file-backed claim is process-scoped."""
    start.wait()
    claim = LearnerEventLedger(Path(path)).claim_derived(
        "claim-e1",
        "bkt",
        now=NOW,
        lease_seconds=60,
    )
    results.put(None if claim is None else claim.token)


def _strong(event_id: str, *, correct: bool, kc: str = "kc1") -> LearnerEvent:
    return LearnerEvent(
        event_id=event_id,
        idempotency_key=f"ik_{event_id}",
        user_id="u1",
        subject_id="math",
        kc_ids=(kc,),
        surface_type="quiz",
        answer_correct=correct,
        evidence_strength="strong",
        attribution_status="reliable",
        created_at=NOW,
    )


def _exposure(event_id: str) -> LearnerEvent:
    # Reading/search/asking/dwell/self-report: never strong evidence.
    return LearnerEvent(
        event_id=event_id,
        idempotency_key=f"ik_{event_id}",
        user_id="u1",
        subject_id="math",
        kc_ids=("kc1",),
        surface_type="reading",
        answer_correct=None,
        evidence_strength="exposure",
        attribution_status="reliable",
        created_at=NOW,
    )


# --- ledger idempotency (invariant #4) --------------------------------------


def test_append_is_idempotent_on_event_id() -> None:
    ledger = LearnerEventLedger()
    event = _strong("e1", correct=True)
    assert ledger.append(event) == "appended"
    assert ledger.append(event) == "duplicate"
    assert len(ledger) == 1


def test_append_is_idempotent_on_idempotency_key() -> None:
    ledger = LearnerEventLedger()
    a = _strong("e1", correct=True)
    b = a.model_copy(update={"event_id": "e2"})  # same idempotency_key, new id
    assert ledger.append(a) == "appended"
    assert ledger.append(b) == "duplicate"
    assert len(ledger) == 1


def test_append_is_atomic_under_concurrent_replay() -> None:
    ledger = LearnerEventLedger()
    event = _strong("concurrent-e1", correct=True)
    barrier = Barrier(16)
    outcomes: list[str] = []

    def append_replay() -> None:
        barrier.wait()
        outcomes.append(ledger.append(event))

    threads = [Thread(target=append_replay) for _ in range(barrier.parties)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert outcomes.count("appended") == 1
    assert outcomes.count("duplicate") == barrier.parties - 1
    assert len(ledger) == 1


def test_persistent_ledger_reloads_and_replays_events(tmp_path) -> None:
    path = tmp_path / "learner-events.json"
    first = LearnerEventLedger(path)
    first.append(_strong("later", correct=False).model_copy(update={"created_at": LATER}))
    first.append(_strong("earlier", correct=True))

    replayed: list[str] = []
    restored = LearnerEventLedger(path, replay=lambda event: replayed.append(event.event_id))

    assert len(restored) == 2
    assert replayed == ["earlier", "later"]


def test_event_is_durable_before_failed_derived_update_and_retry_is_idempotent(tmp_path) -> None:
    path = tmp_path / "learner-events.json"
    event = _strong("e1", correct=True)
    ledger = LearnerEventLedger(path)
    assert ledger.append(event, derived_operations=("bkt",)) == "appended"

    calls = 0

    def fail_once(_event: LearnerEvent) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("derived store unavailable")

    assert ledger.apply_derived("e1", "bkt", fail_once, now=NOW) == "queued"
    restored = LearnerEventLedger(path)
    assert [item.event_id for item in restored.pending_derived()] == ["e1"]
    assert list(restored)[0] == event

    outcomes = restored.retry_failed({"bkt": fail_once}, now=LATER)
    assert set(outcomes.values()) == {"applied"}
    assert restored.pending_derived() == []
    assert restored.apply_derived("e1", "bkt", fail_once, now=LATER) == "already_applied"
    assert calls == 2


def test_concurrent_persistent_instances_do_not_duplicate_event(tmp_path) -> None:
    path = tmp_path / "learner-events.json"
    event = _strong("persistent-concurrent", correct=True)
    barrier = Barrier(12)
    outcomes: list[str] = []

    def append_from_fresh_instance() -> None:
        barrier.wait()
        outcomes.append(LearnerEventLedger(path).append(event))

    threads = [Thread(target=append_from_fresh_instance) for _ in range(barrier.parties)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert outcomes.count("appended") == 1
    assert outcomes.count("duplicate") == barrier.parties - 1
    assert len(LearnerEventLedger(path)) == 1


def test_durable_claim_allows_only_one_process_to_lease_operation(tmp_path) -> None:
    path = tmp_path / "learner-events.json"
    ledger = LearnerEventLedger(path)
    ledger.append(_strong("claim-e1", correct=True), derived_operations=("bkt",))
    context = get_context("spawn")
    start = context.Event()
    results = context.Queue()
    processes = [
        context.Process(target=_claim_in_process, args=(str(path), start, results))
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    start.set()
    for process in processes:
        process.join(timeout=10)

    assert [process.exitcode for process in processes] == [0, 0]
    tokens = [results.get(timeout=1) for _ in processes]
    assert sum(token is not None for token in tokens) == 1


def test_expired_claim_is_taken_over_and_stale_token_is_fenced(tmp_path) -> None:
    path = tmp_path / "learner-events.json"
    first = LearnerEventLedger(path)
    first.append(_strong("lease-e1", correct=True), derived_operations=("bkt",))
    first_claim = first.claim_derived("lease-e1", "bkt", now=NOW, lease_seconds=60)
    assert first_claim is not None

    second = LearnerEventLedger(path)
    assert second.claim_derived("lease-e1", "bkt", now=NOW, lease_seconds=60) is None
    replacement = second.claim_derived("lease-e1", "bkt", now=LATER, lease_seconds=60)
    assert replacement is not None
    assert replacement.token != first_claim.token

    assert (
        first.mark_derived_applied(
            "lease-e1",
            "bkt",
            claim_token=first_claim.token,
        )
        == "claim_lost"
    )
    queued = first.pending_derived()
    assert len(queued) == 1
    assert queued[0].claim_token == replacement.token
    assert (
        second.mark_derived_applied(
            "lease-e1",
            "bkt",
            claim_token=replacement.token,
        )
        == "applied"
    )
    assert first.pending_derived() == []


def test_live_claim_heartbeat_renews_lease_and_fences_stale_tokens(tmp_path) -> None:
    path = tmp_path / "learner-events.json"
    ledger = LearnerEventLedger(path)
    ledger.append(_strong("heartbeat-e1", correct=True), derived_operations=("bkt",))
    claim = ledger.claim_derived("heartbeat-e1", "bkt", now=NOW, lease_seconds=60)
    assert claim is not None

    renewed = ledger.renew_derived_claim(
        "heartbeat-e1",
        "bkt",
        claim_token=claim.token,
        now="2026-08-09T08:00:30+00:00",
        lease_seconds=60,
    )

    assert renewed is not None
    assert renewed.token == claim.token
    assert renewed.lease_expires_at == "2026-08-09T08:01:30+00:00"
    assert (
        LearnerEventLedger(path).claim_derived(
            "heartbeat-e1",
            "bkt",
            now="2026-08-09T08:01:01+00:00",
            lease_seconds=60,
        )
        is None
    )
    assert (
        ledger.renew_derived_claim(
            "heartbeat-e1",
            "bkt",
            claim_token="stale-token-that-is-long-enough",
            now="2026-08-09T08:01:02+00:00",
            lease_seconds=60,
        )
        is None
    )
    assert (
        ledger.renew_derived_claim(
            "heartbeat-e1",
            "bkt",
            claim_token=claim.token,
            now="2026-08-09T08:01:31+00:00",
            lease_seconds=60,
        )
        is None
    )


def test_schema_v1_queue_without_claim_fields_remains_readable(tmp_path) -> None:
    path = tmp_path / "learner-events.json"
    ledger = LearnerEventLedger(path)
    ledger.append(_strong("legacy-e1", correct=True), derived_operations=("bkt",))
    adapter = SectionedRecordStore(
        "learner_events",
        LOCAL_ADMIN_ID,
        schema_version=1,
        legacy_path=path,
    )
    payload = adapter.snapshot()
    payload["derived_queue"][0].pop("claim_token")
    payload["derived_queue"][0].pop("lease_expires_at")
    adapter.replace_all(payload)

    restored = LearnerEventLedger(path)
    assert restored.pending_derived()[0].claim_token is None
    assert restored.apply_derived("legacy-e1", "bkt", lambda _event: None, now=NOW) == "applied"


# --- strong-evidence gate (invariant #2) ------------------------------------


@pytest.mark.parametrize(
    "event",
    [
        _exposure("x1"),  # exposure -> not strong
        _strong("x2", correct=True).model_copy(update={"evidence_strength": "exposure"}),
        _strong("x3", correct=True).model_copy(update={"attribution_status": "weak"}),
        _strong("x4", correct=True).model_copy(
            update={"attribution_status": "attribution_pending"}
        ),
        LearnerEvent(  # self-report: no graded answer
            event_id="x5",
            idempotency_key="ik_x5",
            user_id="u1",
            subject_id="math",
            kc_ids=("kc1",),
            surface_type="chat",
            answer_correct=None,
            evidence_strength="none",
            created_at=NOW,
        ),
    ],
)
def test_non_strong_evidence_excluded(event: LearnerEvent) -> None:
    assert is_strong_evidence(event) is False


def test_only_strong_evidence_is_strong() -> None:
    assert is_strong_evidence(_strong("e1", correct=False)) is True


# --- BKT: strong-evidence-only update + canonical equivalence ---------------


def test_bkt_ignores_exposure_event() -> None:
    store = KnowledgeStateStore()
    key = KnowledgeStateKey(user_id="u1", subject_id="math", kc_id="kc1")
    before = store.get_or_seed(key, now=NOW)
    after = update_with_evidence(before, _exposure("e1"), now=NOW)
    assert after is before  # unchanged object: no BKT movement from exposure
    assert after.verified_observation_count == 0


def test_bkt_updates_on_strong_correct_and_matches_canonical() -> None:
    store = KnowledgeStateStore()
    key = KnowledgeStateKey(user_id="u1", subject_id="math", kc_id="kc1")
    state = store.get_or_seed(key, now=NOW)
    after = update_with_evidence(state, _strong("e1", correct=True), now=NOW)
    assert after.verified_observation_count == 1
    # Equivalence with the canonical personalization update (no drift).
    expected = bkt_update(
        0.2,
        correct=True,
        transition=0.12,
        guess=0.2,
        slip=0.1,
        weight=1.0,
    )
    assert after.mastery_probability == expected
    assert after.mastery_probability > 0.2  # a correct answer raises mastery


def test_bkt_updates_on_strong_incorrect() -> None:
    store = KnowledgeStateStore()
    key = KnowledgeStateKey(user_id="u1", subject_id="math", kc_id="kc1")
    state = store.get_or_seed(key, now=NOW)
    after = update_with_evidence(state, _strong("e1", correct=False), now=NOW)
    assert after.verified_observation_count == 1
    assert after.mastery_probability < 0.2  # an incorrect answer lowers mastery


def test_same_stream_and_param_version_rebuild_consistently() -> None:
    events = [
        _strong("e2", correct=False).model_copy(update={"created_at": LATER}),
        _strong("e1", correct=True),
        _exposure("x1"),
    ]
    first = rebuild_knowledge_states(events)
    second = rebuild_knowledge_states(list(reversed(events)))
    key = KnowledgeStateKey(user_id="u1", subject_id="math", kc_id="kc1")

    assert first.get(key) == second.get(key)
    assert first.get(key).param_version == "v1-uncalibrated"
    assert first.get(key).verified_observation_count == 2


# --- display rule: uncalibrated hides pseudo-precise posterior -------------


def test_display_hides_posterior_when_uncalibrated() -> None:
    store = KnowledgeStateStore()
    key = KnowledgeStateKey(user_id="u1", subject_id="math", kc_id="kc1")
    state = store.get_or_seed(key, now=NOW)
    # Accumulate enough verified observations but stay uncalibrated.
    for i in range(MIN_OBSERVATIONS_FOR_PROBABILITY + 1):
        state = update_with_evidence(state, _strong(f"e{i}", correct=True), now=NOW)
    view = display_mastery(state)
    assert view["mastery_probability"] is None
    assert view["mastery_interval"] == (0.0, 1.0)
    assert view["calibrated"] is False


def test_display_shows_posterior_when_calibrated_and_observed() -> None:
    store = KnowledgeStateStore()
    key = KnowledgeStateKey(user_id="u1", subject_id="math", kc_id="kc1")
    state = store.get_or_seed(key, now=NOW)
    calibrated = BKTParamSet(
        version="v2-cal", transition=0.12, guess=0.2, slip=0.1, prior=0.2, calibrated=True
    )
    for i in range(MIN_OBSERVATIONS_FOR_PROBABILITY):
        state = update_with_evidence(
            state, _strong(f"e{i}", correct=True), params=calibrated, now=NOW
        )
    view = display_mastery(state)
    assert view["mastery_probability"] is not None
    low, high = view["mastery_interval"]
    assert 0.0 <= low < state.mastery_probability < high <= 1.0
    assert high - low > 0.1
    assert view["calibrated"] is True


def test_calibrated_mastery_interval_converges_with_more_observations() -> None:
    sparse = (
        KnowledgeStateStore()
        .get_or_seed(KnowledgeStateKey(user_id="u1", subject_id="math", kc_id="kc1"), now=NOW)
        .model_copy(
            update={
                "mastery_probability": 0.6,
                "verified_observation_count": MIN_OBSERVATIONS_FOR_PROBABILITY,
                "param_version": "v2-cal",
                "calibrated": True,
            }
        )
    )
    dense = sparse.model_copy(update={"verified_observation_count": 300})

    sparse_low, sparse_high = display_mastery(sparse)["mastery_interval"]
    dense_low, dense_high = display_mastery(dense)["mastery_interval"]

    assert sparse_low < sparse_high
    assert dense_low < dense_high
    assert dense_high - dense_low < sparse_high - sparse_low


# --- isolation by user/subject/kc (invariant #6) ----------------------------


def test_state_isolated_per_user_subject_kc() -> None:
    store = KnowledgeStateStore()
    k_u1 = KnowledgeStateKey(user_id="u1", subject_id="math", kc_id="kc1")
    k_u2 = KnowledgeStateKey(user_id="u2", subject_id="math", kc_id="kc1")
    k_u1_other = KnowledgeStateKey(user_id="u1", subject_id="physics", kc_id="kc1")
    s1 = update_with_evidence(
        store.get_or_seed(k_u1, now=NOW), _strong("e1", correct=True), now=NOW
    )
    store.upsert(s1)
    assert store.get(k_u2) is None
    assert store.get(k_u1_other) is None
    assert store.get(k_u1) is not None
    assert len(store.all_for(user_id="u1", subject_id="math")) == 1


def test_get_or_seed_is_atomic_under_concurrency() -> None:
    store = KnowledgeStateStore()
    key = KnowledgeStateKey(user_id="u1", subject_id="math", kc_id="kc1")
    barrier = Barrier(16)
    seeded_states: list[object] = []

    def seed() -> None:
        barrier.wait()
        seeded_states.append(store.get_or_seed(key, now=NOW))

    threads = [Thread(target=seed) for _ in range(barrier.parties)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(seeded_states) == barrier.parties
    assert len({id(state) for state in seeded_states}) == 1
    assert len(store.all_for(user_id="u1", subject_id="math")) == 1


# --- misconception: one error is not a stable misconception -----------------


def test_single_error_cannot_confirm_misconception() -> None:
    store = MisconceptionStore()
    h = store.propose(
        hypothesis_id="h1",
        user_id="u1",
        subject_id="math",
        kc_ids=("kc1",),
        rubric_ref="rubric#sign-error",
        pattern="drops negative on distribution",
        evidence_refs=("err1",),
        created_at=NOW,
    )
    assert h.status == "candidate"
    with pytest.raises(MisconceptionConfirmationError):
        store.confirm("h1", now=LATER)


def test_misconception_confirms_with_repeated_evidence() -> None:
    store = MisconceptionStore()
    store.propose(
        hypothesis_id="h1",
        user_id="u1",
        subject_id="math",
        kc_ids=("kc1",),
        rubric_ref="rubric#sign-error",
        pattern="drops negative on distribution",
        created_at=NOW,
    )
    for i in range(MISCONCEPTION_EVIDENCE_THRESHOLD):
        store.add_evidence("h1", f"err{i}", now=NOW)
    confirmed = store.confirm("h1", now=LATER)
    assert confirmed.status == "confirmed"
    # Resolve links, never deletes: the original evidence survives.
    resolved = store.resolve("h1", now=LATER)
    assert resolved.status == "resolved"
    assert len(resolved.evidence_refs) == MISCONCEPTION_EVIDENCE_THRESHOLD


def test_misconception_evidence_is_deduplicated() -> None:
    store = MisconceptionStore()
    store.propose(
        hypothesis_id="h1",
        user_id="u1",
        subject_id="math",
        kc_ids=("kc1",),
        rubric_ref="rubric#sign-error",
        pattern="drops negative",
        created_at=NOW,
    )
    store.add_evidence("h1", "err1", now=NOW)
    store.add_evidence("h1", "err1", now=NOW)  # same ref twice -> no inflation
    assert store.get("h1").evidence_refs == ("err1",)


def test_misconception_evidence_updates_are_atomic_under_concurrency() -> None:
    store = MisconceptionStore()
    store.propose(
        hypothesis_id="h1",
        user_id="u1",
        subject_id="math",
        kc_ids=("kc1",),
        rubric_ref="rubric#sign-error",
        pattern="drops negative",
        created_at=NOW,
    )
    barrier = Barrier(16)

    def add_distinct_evidence(index: int) -> None:
        barrier.wait()
        store.add_evidence("h1", f"err{index}", now=LATER)

    threads = [
        Thread(target=add_distinct_evidence, args=(index,)) for index in range(barrier.parties)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    item = store.get("h1")
    assert item is not None
    assert set(item.evidence_refs) == {f"err{index}" for index in range(barrier.parties)}
