from __future__ import annotations

import asyncio

import pytest

from traittutor import learning_packs
from traittutor.api.routers.learning_packs import (
    RepairRetryRequest,
    ReviewResultRequest,
    _canonical_repair_provenance,
    _learner_pack,
    _record_learning_repair_retry,
    _record_learning_review_result,
    record_learning_repair_retry,
)
from traittutor.learning.event_chain import CanonicalAnswerEventChain
from traittutor.learning_model import KnowledgeStateKey, LearnerEventLedger, is_strong_evidence
from traittutor.services.path_service import PathService


class _Recorder:
    def __init__(self) -> None:
        self.events: list[object] = []

    async def record_event(self, event: object, *, trusted: bool) -> list[object]:
        assert trusted is True
        self.events.append(event)
        return []


def _chain(tmp_path):
    recorder = _Recorder()
    return (
        CanonicalAnswerEventChain(
            LearnerEventLedger(tmp_path / "events.json"),
            personalization_service_factory=lambda: recorder,
        ),
        recorder,
    )


def _seed_due_repair(
    tmp_path,
    monkeypatch,
    *,
    chain: CanonicalAnswerEventChain,
    source_user_id: str,
    source_subject_id: str,
    repair_subject_id: str,
    repair_kc_id: str,
):
    service = PathService(workspace_root=tmp_path / "workspace")
    monkeypatch.setattr("traittutor.learning_packs.get_path_service", lambda: service)
    pack = learning_packs.create_pack(title="Algebra", goal="Review equations")
    binding, replayed = learning_packs.create_learning_path_binding(
        pack["pack_id"],
        owner_id=source_user_id,
        learning_path_id="path-math",
        subject_id=source_subject_id,
        allowed_kc_ids=[repair_kc_id],
        graph_fingerprint="test-graph",
        graph_version=1,
    )
    assert replayed is False
    assert binding["learning_path_id"] == "path-math"
    source_event, _ = chain.record_server_graded(
        user_id=source_user_id,
        subject_id=source_subject_id,
        question_id="assessment-q",
        kc_ids=(repair_kc_id,),
        is_correct=False,
        item_valid=True,
        attribution_reliable=True,
        derived=lambda _event: None,
        attempt_id="assessment-attempt",
        surface_type="practice",
        learning_path_id="path-math",
    )
    repair = learning_packs.create_repair(
        pack["pack_id"],
        action_id="assessment",
        question_id="assessment-q",
        artifact_ref="artifact-1",
        concept_id=repair_kc_id,
        user_answer="wrong",
        correct_rule="server rule",
        retry_prompt="Solve again",
        retry_expected_answer="B",
        retry_question_id="review-q",
        source_event_id="component-attempt",
        canonical_source_event_id=source_event.event_id,
        review_owner_id=source_user_id,
        review_subject_id=repair_subject_id,
        review_kc_id=repair_kc_id,
    )
    assert repair is not None
    review_id = f"review-repair-{repair['repair_id']}"
    with learning_packs._locked_packs() as packs:
        stored = next(item for item in packs if item["pack_id"] == pack["pack_id"])
        stored["review_states"] = [
            {
                "review_id": review_id,
                "pack_id": pack["pack_id"],
                "concept_id": repair_kc_id,
                "source": "repair",
                "due_at": "2020-01-01T00:00:00+00:00",
                "priority": 1,
                "interval_index": 0,
                "consecutive_correct": 0,
                "consecutive_wrong": 0,
                "last_result": None,
            }
        ]
        learning_packs._save(packs)
    return pack["pack_id"], review_id, source_event


def test_repair_review_is_event_first_and_replay_does_not_double_bkt(tmp_path, monkeypatch) -> None:
    chain, recorder = _chain(tmp_path)
    source_event, source_subject, source_kc = _canonical_repair_provenance(
        chain=chain,
        user_id="owner-a",
        component_attempt_id="assessment-attempt",
        subject_id="math",
        kc_id="equations",
    )
    # No source has been appended yet, so a repair can retain only its
    # server-derived partition and not fabricate canonical provenance.
    assert source_event == ""
    assert (source_subject, source_kc) == ("math", "equations")

    pack_id, review_id, source = _seed_due_repair(
        tmp_path,
        monkeypatch,
        chain=chain,
        source_user_id="owner-a",
        source_subject_id="math",
        repair_subject_id="math",
        repair_kc_id="equations",
    )
    canonical_source, subject_id, kc_id = _canonical_repair_provenance(
        chain=chain,
        user_id="owner-a",
        component_attempt_id="assessment-attempt",
        subject_id="math",
        kc_id="equations",
    )
    assert (canonical_source, subject_id, kc_id) == (source.event_id, "math", "equations")
    fallback_source, fallback_subject, fallback_kc = _canonical_repair_provenance(
        chain=chain,
        user_id="owner-a",
        component_attempt_id="assessment-attempt",
        subject_id="math",
        kc_id="geometry",
    )
    assert (fallback_source, fallback_subject, fallback_kc) == ("", "math", "geometry")

    request = ReviewResultRequest(event_id="review-attempt-1", answer="B")
    first = asyncio.run(
        _record_learning_review_result(
            pack_id,
            review_id,
            request,
            user_id="owner-a",
            chain=chain,
        )
    )
    replay = asyncio.run(
        _record_learning_review_result(
            pack_id,
            review_id,
            request,
            user_id="owner-a",
            chain=chain,
        )
    )

    assert first["verified"] is True and replay["verified"] is True
    assert len(chain.ledger) == 2  # original assessment + one review event
    state = chain.rebuild_bkt().get(
        KnowledgeStateKey(user_id="owner-a", subject_id="math", kc_id="equations")
    )
    assert state is not None and state.verified_observation_count == 2
    assert len(recorder.events) == 2
    restored = learning_packs.get_pack(pack_id)
    assert restored is not None and len(restored["review_attempts"]) == 1


def test_repair_retry_writes_canonical_event_before_schedule_and_replay_is_idempotent(
    tmp_path, monkeypatch
) -> None:
    chain, recorder = _chain(tmp_path)
    pack_id, _review_id, source = _seed_due_repair(
        tmp_path,
        monkeypatch,
        chain=chain,
        source_user_id="owner-a",
        source_subject_id="math",
        repair_subject_id="math",
        repair_kc_id="equations",
    )
    stored_before = learning_packs.get_pack(pack_id)
    assert stored_before is not None
    repair = stored_before["repairs"][0]
    assert {
        "canonical_source_event_id": source.event_id,
        "review_owner_id": "owner-a",
        "review_subject_id": "math",
        "review_kc_id": "equations",
    }.items() <= repair.items()

    first = asyncio.run(
        _record_learning_repair_retry(
            pack_id,
            repair["repair_id"],
            RepairRetryRequest(event_id="retry-attempt-1", answer="B"),
            user_id="owner-a",
            chain=chain,
        )
    )
    replay = asyncio.run(
        _record_learning_repair_retry(
            pack_id,
            repair["repair_id"],
            RepairRetryRequest(event_id="retry-attempt-1", answer="B"),
            user_id="owner-a",
            chain=chain,
        )
    )

    assert first["verified_correct"] is replay["verified_correct"] is True
    assert len(chain.ledger) == 2  # server-graded assessment + retry
    state = chain.rebuild_bkt().get(
        KnowledgeStateKey(user_id="owner-a", subject_id="math", kc_id="equations")
    )
    assert state is not None and state.verified_observation_count == 2
    assert len(recorder.events) == 2
    restored = learning_packs.get_pack(pack_id)
    assert restored is not None
    retry = restored["repairs"][0]
    assert retry["status"] == "scheduled"
    assert len(retry["retry_attempts"]) == 1


def test_repair_retry_uses_one_transaction_when_pack_and_events_share_database(
    tmp_path, monkeypatch
) -> None:
    """Production co-locates Pack and event rows in one SQLite database."""
    service = PathService(workspace_root=tmp_path / "workspace")
    monkeypatch.setattr("traittutor.learning_packs.get_path_service", lambda: service)
    ledger = LearnerEventLedger(
        service.get_workspace_dir() / "learning_model" / "learner_events.json",
        path_service=service,
    )
    chain = CanonicalAnswerEventChain(
        ledger,
        personalization_service_factory=lambda: _Recorder(),
    )
    pack = learning_packs.create_pack(title="Legacy pack", goal="Repair one answer")
    repair = learning_packs.create_repair(
        pack["pack_id"],
        action_id="assessment",
        question_id="assessment-q",
        artifact_ref="artifact-1",
        concept_id="concept-1",
        user_answer="wrong",
        correct_rule="server rule",
        retry_prompt="Solve again",
        retry_expected_answer="B",
        retry_question_id="review-q",
    )
    assert repair is not None

    result = asyncio.run(
        _record_learning_repair_retry(
            pack["pack_id"],
            repair["repair_id"],
            RepairRetryRequest(event_id="retry-attempt-1", answer="B"),
            user_id="owner-a",
            chain=chain,
        )
    )

    assert result["verified_correct"] is True
    assert len(chain.ledger) == 1
    assert not is_strong_evidence(next(iter(chain.ledger)))
    restored = learning_packs.get_pack(pack["pack_id"])
    assert restored is not None
    assert restored["repairs"][0]["status"] == "scheduled"


def test_repair_retry_server_verdict_callback_precedes_legacy_mutation(
    tmp_path, monkeypatch
) -> None:
    """A canonical callback failure cannot leave a retry scheduled without its event."""
    chain, _recorder = _chain(tmp_path)
    pack_id, _review_id, _source = _seed_due_repair(
        tmp_path,
        monkeypatch,
        chain=chain,
        source_user_id="owner-a",
        source_subject_id="math",
        repair_subject_id="math",
        repair_kc_id="equations",
    )
    repair_id = learning_packs.get_pack(pack_id)["repairs"][0]["repair_id"]
    observed: list[bool] = []

    def before_mutation(repair, correct) -> None:
        assert repair["repair_id"] == repair_id
        assert repair.get("retry_attempts") in (None, [])
        assert repair["retry_count"] == 0
        assert "last_retry_correct" not in repair
        assert repair["status"] == "identified"
        observed.append(correct)

    result = learning_packs.record_repair_retry(
        pack_id,
        repair_id,
        answer="B",
        event_id="retry-attempt-1",
        before_mutation=before_mutation,
    )

    assert observed == [True]
    assert result is not None and result["status"] == "scheduled"


def test_repair_retry_owner_or_subject_mismatch_is_weak_and_bkt_noop(tmp_path, monkeypatch) -> None:
    chain, recorder = _chain(tmp_path)
    pack_id, _review_id, _source = _seed_due_repair(
        tmp_path,
        monkeypatch,
        chain=chain,
        source_user_id="owner-b",
        source_subject_id="physics",
        repair_subject_id="math",
        repair_kc_id="equations",
    )
    repair = learning_packs.get_pack(pack_id)["repairs"][0]

    result = asyncio.run(
        _record_learning_repair_retry(
            pack_id,
            repair["repair_id"],
            RepairRetryRequest(event_id="retry-attempt-1", answer="B"),
            user_id="owner-a",
            chain=chain,
        )
    )

    assert result["verified_correct"] is True
    weak_events = [
        event
        for event in chain.ledger
        if event.user_id == "owner-a" and not is_strong_evidence(event)
    ]
    assert len(weak_events) == 1
    assert weak_events[0].subject_id is None
    assert chain.rebuild_bkt().all_for(user_id="owner-a", subject_id="math") == []
    assert len(recorder.events) == 1  # only the foreign source event projected


def test_revealed_original_question_retry_is_weak_and_bkt_noop(tmp_path, monkeypatch) -> None:
    chain, _recorder = _chain(tmp_path)
    pack_id, _review_id, _source = _seed_due_repair(
        tmp_path,
        monkeypatch,
        chain=chain,
        source_user_id="owner-a",
        source_subject_id="math",
        repair_subject_id="math",
        repair_kc_id="equations",
    )
    with learning_packs._locked_packs() as packs:
        stored = next(item for item in packs if item["pack_id"] == pack_id)
        repair = stored["repairs"][0]
        repair["retry_question_id"] = repair["question_id"]
        repair["retry_evidence_strength"] = "weak"
        learning_packs._save(packs)

    repair = learning_packs.get_pack(pack_id)["repairs"][0]
    result = asyncio.run(
        _record_learning_repair_retry(
            pack_id,
            repair["repair_id"],
            RepairRetryRequest(event_id="same-item-attempt", answer="B"),
            user_id="owner-a",
            chain=chain,
        )
    )

    assert result["evidence_strength"] == "weak"
    state = chain.rebuild_bkt().get(
        KnowledgeStateKey(user_id="owner-a", subject_id="math", kc_id="equations")
    )
    assert state is not None and state.verified_observation_count == 1
    assert len(chain.ledger) == 2
    assert not is_strong_evidence(list(chain.ledger)[-1])


@pytest.mark.parametrize(
    ("source_user_id", "source_subject_id"),
    (("owner-b", "math"), ("owner-a", "physics")),
)
def test_repair_review_owner_or_subject_mismatch_is_weak_and_bkt_noop(
    tmp_path,
    monkeypatch,
    source_user_id: str,
    source_subject_id: str,
) -> None:
    chain, recorder = _chain(tmp_path)
    pack_id, review_id, _source = _seed_due_repair(
        tmp_path,
        monkeypatch,
        chain=chain,
        source_user_id=source_user_id,
        source_subject_id=source_subject_id,
        repair_subject_id="math",
        repair_kc_id="equations",
    )

    asyncio.run(
        _record_learning_review_result(
            pack_id,
            review_id,
            ReviewResultRequest(event_id="review-attempt-1", answer="B"),
            user_id="owner-a",
            chain=chain,
        )
    )

    owner_a_events = [event for event in chain.ledger if event.user_id == "owner-a"]
    weak_events = [event for event in owner_a_events if not is_strong_evidence(event)]
    assert len(weak_events) == 1
    assert weak_events[0].subject_id is None
    assert chain.rebuild_bkt().all_for(user_id="owner-a", subject_id="math") == []
    # The source belongs to another partition and is never projected for the
    # caller who submitted the review.
    assert len(recorder.events) == 1


def test_retrieval_rating_updates_schedule_but_never_bkt(tmp_path, monkeypatch) -> None:
    service = PathService(workspace_root=tmp_path / "workspace")
    monkeypatch.setattr("traittutor.learning_packs.get_path_service", lambda: service)
    chain, recorder = _chain(tmp_path)
    pack = learning_packs.create_pack(title="Algebra", goal="Review equations")
    with learning_packs._locked_packs() as packs:
        stored = next(item for item in packs if item["pack_id"] == pack["pack_id"])
        stored["review_states"] = [
            {
                "review_id": "retrieval-1",
                "pack_id": pack["pack_id"],
                "concept_id": "equations",
                "source": "retrieval",
                "due_at": "2020-01-01T00:00:00+00:00",
                "priority": 1,
                "interval_index": 0,
                "consecutive_correct": 0,
                "consecutive_wrong": 0,
                "last_result": None,
            }
        ]
        learning_packs._save(packs)

    result = asyncio.run(
        _record_learning_review_result(
            pack["pack_id"],
            "retrieval-1",
            ReviewResultRequest(event_id="retrieval-attempt-1", rating="known"),
            user_id="owner-a",
            chain=chain,
        )
    )

    assert result["verified"] is False
    assert len(chain.ledger) == 0
    assert recorder.events == []


def test_repair_review_provenance_stays_server_side() -> None:
    public = _learner_pack(
        {
            "repairs": [
                {
                    "repair_id": "repair-1",
                    "status": "identified",
                    "retry_expected_answer": "B",
                    "canonical_source_event_id": "answer-private",
                    "review_owner_id": "owner-a",
                    "review_subject_id": "math",
                    "review_kc_id": "equations",
                    "retry_question_id": "review-q",
                }
            ]
        }
    )

    repair = public["repairs"][0]
    assert {
        "retry_expected_answer",
        "canonical_source_event_id",
        "review_owner_id",
        "review_subject_id",
        "review_kc_id",
        "retry_question_id",
    }.isdisjoint(repair)


def test_repair_retry_response_omits_review_provenance(monkeypatch) -> None:
    monkeypatch.setattr(
        "traittutor.api.routers.learning_packs.learning_packs.record_repair_retry",
        lambda *_args, **_kwargs: {
            "repair_id": "repair-1",
            "retry_expected_answer": "B",
            "canonical_source_event_id": "answer-private",
            "review_owner_id": "owner-a",
            "review_subject_id": "math",
            "review_kc_id": "equations",
            "retry_question_id": "review-q",
        },
    )
    result = asyncio.run(
        record_learning_repair_retry(
            "pack-1",
            "repair-1",
            RepairRetryRequest(event_id="retry-attempt-1", answer="B"),
        )
    )

    assert {
        "retry_expected_answer",
        "canonical_source_event_id",
        "review_owner_id",
        "review_subject_id",
        "review_kc_id",
        "retry_question_id",
    }.isdisjoint(result["repair"])
