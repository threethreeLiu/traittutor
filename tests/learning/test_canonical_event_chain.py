from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from traittutor.learning.event_chain import CanonicalAnswerEventChain, stable_answer_identity
from traittutor.learning.models import (
    KnowledgePoint,
    KnowledgeType,
    LearningModule,
    LearningProgress,
    PendingQuestion,
)
from traittutor.learning.service import LearningService
from traittutor.learning.storage import LearningStore
from traittutor.learning_model import (
    BKT_PARAMETERS_PATH_ENV,
    DEFAULT_PARAMS,
    BKTParameterConfigurationError,
    KnowledgeStateKey,
    LearnerEvent,
    LearnerEventLedger,
    is_strong_evidence,
)
from traittutor.personalization.models import LearningSignal, SubjectRef
from traittutor.personalization.service import (
    CANONICAL_BKT_PARAM_VERSION,
    PersonalizationService,
)


class _PersonalizationRecorder:
    def __init__(self) -> None:
        self.events: list[object] = []

    async def record_event(self, event: object, *, trusted: bool) -> list[object]:
        assert trusted is True
        self.events.append(event)
        return []


def _chain(path: Path) -> CanonicalAnswerEventChain:
    recorder = _PersonalizationRecorder()
    return CanonicalAnswerEventChain(
        LearnerEventLedger(path),
        personalization_service_factory=lambda: recorder,
    )


def test_server_graded_answer_is_event_first_and_replay_does_not_rescore(tmp_path) -> None:
    chain = _chain(tmp_path / "events.json")
    projection_calls: list[str] = []

    def project(event: object) -> None:
        # The event snapshot is already durable before the callback begins.
        assert len(LearnerEventLedger(tmp_path / "events.json")) == 1
        projection_calls.append(str(event.event_id))

    first, first_outcome = chain.record_server_graded(
        user_id="u1",
        subject_id="math",
        question_id="q1",
        kc_ids=("kc1",),
        attempt_id="attempt-1",
        is_correct=True,
        item_valid=True,
        attribution_reliable=True,
        derived=project,
    )
    second, second_outcome = chain.record_server_graded(
        user_id="u1",
        subject_id="math",
        question_id="q1",
        kc_ids=("kc1",),
        attempt_id="attempt-1",
        is_correct=True,
        item_valid=True,
        attribution_reliable=True,
        derived=project,
    )

    assert first == second
    assert first_outcome == "applied"
    assert second_outcome == "already_applied"
    assert projection_calls == [first.event_id]
    state = chain.rebuild_bkt().get(KnowledgeStateKey(user_id="u1", subject_id="math", kc_id="kc1"))
    assert state is not None
    assert state.verified_observation_count == 1


def test_distinct_attempts_with_identical_answer_are_distinct_events(tmp_path, monkeypatch) -> None:
    chain = _chain(tmp_path / "events.json")
    service = LearningService(LearningStore(tmp_path / "progress"), event_chain=chain)
    progress = LearningProgress(
        book_id="book1",
        modules=[
            LearningModule(
                id="m1",
                name="Math",
                order=1,
                knowledge_points=[
                    KnowledgePoint(
                        id="kc1",
                        name="Addition",
                        type=KnowledgeType.PROCEDURE,
                        module_id="m1",
                    )
                ],
            )
        ],
        knowledge_types={"kc1": KnowledgeType.PROCEDURE},
        pending_question=PendingQuestion(
            question_id="q1",
            knowledge_point_id="kc1",
            module_id="m1",
            expected_answer="4",
        ),
    )

    for attempt_id in ("attempt-1", "attempt-2"):
        assert service.grade_and_record(
            progress,
            question_id="q1",
            knowledge_point_id="kc1",
            module_id="m1",
            user_answer="4",
            expected_answer="4",
            user_id="u1",
            subject_id="math",
            attempt_id=attempt_id,
        )

    assert len(chain.ledger) == 2
    assert len(progress.quiz_attempts) == 2
    state = chain.rebuild_bkt().get(KnowledgeStateKey(user_id="u1", subject_id="math", kc_id="kc1"))
    assert state is not None
    assert state.verified_observation_count == 2
    assert stable_answer_identity(user_id="u1", attempt_id="attempt-1") != (
        stable_answer_identity(user_id="u1", attempt_id="attempt-2")
    )


def test_untrusted_judge_submission_is_attribution_pending_and_never_bkt(tmp_path) -> None:
    chain = _chain(tmp_path / "events.json")
    event = chain.record_ungraded_submission(
        user_id="u1", subject_id="math", question_id="q1", attempt_id="attempt-1"
    )

    assert event.answer_correct is None
    assert event.attribution_status == "attribution_pending"
    assert is_strong_evidence(event) is False
    assert chain.rebuild_bkt().all_for(user_id="u1", subject_id="math") == []


def test_invalid_or_unattributed_server_item_does_not_update_bkt(tmp_path) -> None:
    chain = _chain(tmp_path / "events.json")
    event, _ = chain.record_server_graded(
        user_id="u1",
        subject_id="math",
        question_id="q1",
        kc_ids=(),
        attempt_id="attempt-1",
        is_correct=True,
        item_valid=False,
        attribution_reliable=False,
        derived=lambda _event: None,
    )

    assert event.attribution_status == "attribution_pending"
    assert chain.rebuild_bkt().all_for(user_id="u1", subject_id="math") == []


def test_learning_service_uses_one_canonical_event_chain(tmp_path) -> None:
    chain = _chain(tmp_path / "events.json")
    service = LearningService(LearningStore(tmp_path / "progress"), event_chain=chain)
    progress = LearningProgress(
        book_id="book1",
        modules=[
            LearningModule(
                id="m1",
                name="Math",
                order=1,
                knowledge_points=[
                    KnowledgePoint(
                        id="kc1",
                        name="Addition",
                        type=KnowledgeType.PROCEDURE,
                        module_id="m1",
                    )
                ],
            )
        ],
        knowledge_types={"kc1": KnowledgeType.PROCEDURE},
        pending_question=PendingQuestion(
            question_id="q1",
            knowledge_point_id="kc1",
            module_id="m1",
            expected_answer="4",
        ),
    )

    assert service.grade_and_record(
        progress,
        question_id="q1",
        knowledge_point_id="kc1",
        module_id="m1",
        user_answer="4",
        expected_answer="4",
        user_id="u1",
        subject_id="math",
    )
    assert len(progress.quiz_attempts) == 1
    assert progress.subject_id == "math"
    read_view = service.mastery_read_view(progress, user_id="u1")
    assert read_view is not None
    assert read_view.read("kc1").verified_observation_count == 1
    assert (
        chain.rebuild_bkt()
        .get(KnowledgeStateKey(user_id="u1", subject_id="math", kc_id="kc1"))
        .verified_observation_count
        == 1
    )
    assert progress.verified_observation_counts == {"kc1": 1}
    assert progress.mastery_intervals == {"kc1": (0.0, 1.0)}


def test_learning_service_without_server_item_writes_pending_event_only(
    tmp_path, monkeypatch
) -> None:
    chain = _chain(tmp_path / "events.json")
    service = LearningService(LearningStore(tmp_path / "progress"), event_chain=chain)
    progress = LearningProgress(book_id="book1")

    assert service.grade_and_record(
        progress,
        question_id="forged",
        knowledge_point_id="forged-kc",
        module_id="forged-module",
        user_answer="4",
        expected_answer="4",
        user_id="u1",
        subject_id="math",
        attempt_id="attempt-forged",
    )

    assert progress.quiz_attempts == []
    event = list(chain.ledger)[0]
    assert event.attribution_status == "attribution_pending"
    assert is_strong_evidence(event) is False
    assert chain.rebuild_bkt().all_for(user_id="u1", subject_id="math") == []


def test_missing_subject_stays_pending_without_fabricated_partition(tmp_path, monkeypatch) -> None:
    chain = _chain(tmp_path / "events.json")
    service = LearningService(LearningStore(tmp_path / "progress"), event_chain=chain)
    progress = LearningProgress(
        book_id="book1",
        modules=[
            LearningModule(
                id="m1",
                name="Math",
                order=1,
                knowledge_points=[
                    KnowledgePoint(
                        id="kc1",
                        name="Addition",
                        type=KnowledgeType.PROCEDURE,
                        module_id="m1",
                    )
                ],
            )
        ],
        pending_question=PendingQuestion(
            question_id="q1",
            knowledge_point_id="kc1",
            module_id="m1",
            expected_answer="4",
        ),
    )

    assert service.grade_and_record(
        progress,
        question_id="q1",
        knowledge_point_id="kc1",
        module_id="m1",
        user_answer="4",
        expected_answer="4",
        user_id="u1",
        attempt_id="attempt-no-subject",
    )

    event = list(chain.ledger)[0]
    assert event.subject_id is None
    assert event.attribution_status == "attribution_pending"
    assert progress.quiz_attempts == []
    state_store = chain.rebuild_bkt()
    assert state_store.all_for(user_id="u1", subject_id="m1") == []
    assert state_store.all_for(user_id="u1", subject_id="book1") == []


def test_canonical_chain_projects_once_to_versioned_personalization_bkt(
    tmp_path, monkeypatch
) -> None:
    service = PersonalizationService()
    monkeypatch.setattr(service, "_root", lambda: tmp_path / "learner")
    chain = CanonicalAnswerEventChain(
        LearnerEventLedger(tmp_path / "events.json"),
        personalization_service_factory=lambda: service,
    )

    for _ in range(2):
        chain.record_server_graded(
            user_id="local-admin",
            subject_id="math",
            question_id="q1",
            kc_ids=("kc1",),
            attempt_id="attempt-1",
            is_correct=True,
            item_valid=True,
            attribution_reliable=True,
            derived=lambda _event: None,
            module_id="m1",
        )

    profile = service.subject_profile("math")
    assert len(profile.concept_signals) == 1
    signal = profile.concept_signals[0]
    assert signal.verified_observation_count == 1
    assert signal.bkt_param_version == CANONICAL_BKT_PARAM_VERSION
    assert signal.bkt_calibrated is False
    assert signal.mastery_interval == (0.0, 1.0)
    public_signal = profile.model_dump()["concept_signals"][0]
    internal_signal = profile.model_dump(context={"include_uncalibrated_posterior": True})[
        "concept_signals"
    ][0]
    assert public_signal["mastery_probability"] is None
    assert isinstance(internal_signal["mastery_probability"], float)


def test_canonical_live_projection_matches_ledger_rebuild_parameters_and_posterior(
    tmp_path, monkeypatch
) -> None:
    """The live canonical projection and a replay use one BKT source of truth."""
    service = PersonalizationService()
    monkeypatch.setattr(service, "_root", lambda: tmp_path / "learner")
    chain = CanonicalAnswerEventChain(
        LearnerEventLedger(tmp_path / "events.json"),
        personalization_service_factory=lambda: service,
    )

    for attempt_id, is_correct in (("attempt-1", True), ("attempt-2", False)):
        chain.record_server_graded(
            user_id="local-admin",
            subject_id="math",
            question_id="q1",
            kc_ids=("kc1",),
            attempt_id=attempt_id,
            is_correct=is_correct,
            item_valid=True,
            attribution_reliable=True,
            derived=lambda _event: None,
        )

    live = service.subject_profile("math").concept_signals[0]
    rebuilt = chain.rebuild_bkt().get(
        KnowledgeStateKey(user_id="local-admin", subject_id="math", kc_id="kc1")
    )

    assert rebuilt is not None
    assert live.verified_observation_count == rebuilt.verified_observation_count == 2
    assert live.mastery_probability == pytest.approx(rebuilt.mastery_probability)
    assert (
        live.initial_mastery_probability
        == rebuilt.initial_mastery_probability
        == DEFAULT_PARAMS.prior
    )
    assert live.transition_probability == DEFAULT_PARAMS.transition
    assert live.guess_probability == DEFAULT_PARAMS.guess
    assert live.slip_probability == DEFAULT_PARAMS.slip
    assert live.bkt_param_version == rebuilt.param_version == DEFAULT_PARAMS.version
    assert live.bkt_calibrated is rebuilt.calibrated is DEFAULT_PARAMS.calibrated


def test_async_projection_strong_ref_runs_to_completion(tmp_path: Path) -> None:
    # Regression for code-review finding #9: the fire-and-forget BKT projection
    # scheduled on a running loop must hold a strong reference until completion.
    # Without it, CPython may collect the task before the scheduler runs it, the
    # done_callback never fires, and the projection stays pending forever.
    recorder = _PersonalizationRecorder()
    chain = CanonicalAnswerEventChain(
        LearnerEventLedger(tmp_path / "events.json"),
        personalization_service_factory=lambda: recorder,
    )

    async def driver() -> None:
        chain.record_server_graded(
            user_id="u1",
            subject_id="math",
            question_id="q1",
            kc_ids=("kc1",),
            attempt_id="attempt-1",
            is_correct=True,
            item_valid=True,
            attribution_reliable=True,
            derived=lambda _event: None,
        )
        # Drain the scheduled projection task; if the strong ref were missing
        # the task would be GC'd before running and recorder would stay empty.
        loop = asyncio.get_running_loop()
        pending = [t for t in asyncio.all_tasks(loop) if t is not asyncio.current_task()]
        while pending:
            await asyncio.gather(*pending, return_exceptions=True)
            pending = [t for t in asyncio.all_tasks(loop) if t is not asyncio.current_task()]

    asyncio.run(driver())
    assert len(recorder.events) == 1
    # Intentional white-box coverage of the task-GC fix: the strong-ref set is
    # populated on schedule and cleared on completion, so an empty set here
    # proves the done_callback fired (it discards the finished task).
    assert chain._bg_tasks == set()


def test_async_personalization_claim_prevents_cross_instance_double_schedule(
    tmp_path: Path,
) -> None:
    path = tmp_path / "events.json"
    ledger = LearnerEventLedger(path)
    event = LearnerEvent(
        event_id="claim-e1",
        idempotency_key="claim-ik1",
        user_id="u1",
        subject_id="math",
        kc_ids=("kc1",),
        surface_type="quiz",
        answer_correct=True,
        evidence_strength="strong",
        attribution_status="reliable",
        created_at="2026-08-09T08:00:00+00:00",
    )
    ledger.append(event, derived_operations=("personalization-bkt",))

    class BlockingRecorder:
        def __init__(self) -> None:
            self.calls = 0
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def record_event(self, _event: object, *, trusted: bool) -> list[object]:
            assert trusted is True
            self.calls += 1
            self.started.set()
            await self.release.wait()
            return []

    async def driver() -> None:
        first_recorder = BlockingRecorder()
        second_recorder = BlockingRecorder()
        first = CanonicalAnswerEventChain(
            LearnerEventLedger(path),
            personalization_service_factory=lambda: first_recorder,
        )
        second = CanonicalAnswerEventChain(
            LearnerEventLedger(path),
            personalization_service_factory=lambda: second_recorder,
        )

        assert first.project_personalization(event.event_id, now=event.created_at) == "queued"
        await first_recorder.started.wait()
        assert second.project_personalization(event.event_id, now=event.created_at) == "queued"
        assert second_recorder.calls == 0
        first_recorder.release.set()
        await asyncio.gather(*first._bg_tasks)
        assert first_recorder.calls == 1
        assert LearnerEventLedger(path).pending_derived() == []

    asyncio.run(driver())


def test_async_personalization_heartbeat_keeps_long_projection_claimed(
    tmp_path: Path,
) -> None:
    path = tmp_path / "events.json"
    created_at = datetime.now(UTC).isoformat()
    ledger = LearnerEventLedger(path)
    event = LearnerEvent(
        event_id="heartbeat-e1",
        idempotency_key="heartbeat-ik1",
        user_id="u1",
        subject_id="math",
        kc_ids=("kc1",),
        surface_type="quiz",
        answer_correct=True,
        evidence_strength="strong",
        attribution_status="reliable",
        created_at=created_at,
    )
    ledger.append(event, derived_operations=("personalization-bkt",))

    class BlockingRecorder:
        def __init__(self) -> None:
            self.calls = 0
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def record_event(self, _event: object, *, trusted: bool) -> list[object]:
            assert trusted is True
            self.calls += 1
            self.started.set()
            await self.release.wait()
            return []

    async def driver() -> None:
        first_recorder = BlockingRecorder()
        second_recorder = BlockingRecorder()
        first = CanonicalAnswerEventChain(
            LearnerEventLedger(path),
            personalization_service_factory=lambda: first_recorder,
            personalization_claim_lease_seconds=0.09,
        )
        second = CanonicalAnswerEventChain(
            LearnerEventLedger(path),
            personalization_service_factory=lambda: second_recorder,
            personalization_claim_lease_seconds=0.09,
        )

        assert first.project_personalization(event.event_id, now=created_at) == "queued"
        await first_recorder.started.wait()
        # Longer than the original lease: without heartbeat a second worker
        # could now take over and duplicate the expensive callback.
        await asyncio.sleep(0.14)
        assert (
            second.project_personalization(
                event.event_id,
                now=datetime.now(UTC).isoformat(),
            )
            == "queued"
        )
        assert second_recorder.calls == 0
        first_recorder.release.set()
        await asyncio.gather(*first._bg_tasks)
        assert LearnerEventLedger(path).pending_derived() == []

    asyncio.run(driver())


def test_cancelled_personalization_releases_claim_for_immediate_retry(tmp_path: Path) -> None:
    path = tmp_path / "events.json"
    ledger = LearnerEventLedger(path)
    event = LearnerEvent(
        event_id="cancel-e1",
        idempotency_key="cancel-ik1",
        user_id="u1",
        subject_id="math",
        kc_ids=("kc1",),
        surface_type="quiz",
        answer_correct=True,
        evidence_strength="strong",
        attribution_status="reliable",
        created_at="2026-08-09T08:00:00+00:00",
    )
    ledger.append(event, derived_operations=("personalization-bkt",))

    class BlockingRecorder:
        def __init__(self) -> None:
            self.started = asyncio.Event()

        async def record_event(self, _event: object, *, trusted: bool) -> list[object]:
            assert trusted is True
            self.started.set()
            await asyncio.Event().wait()
            return []

    async def driver() -> None:
        recorder = BlockingRecorder()
        chain = CanonicalAnswerEventChain(
            LearnerEventLedger(path),
            personalization_service_factory=lambda: recorder,
        )
        assert chain.project_personalization(event.event_id, now=event.created_at) == "queued"
        await recorder.started.wait()
        task = next(iter(chain._bg_tasks))
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)

        pending = LearnerEventLedger(path).pending_derived()
        assert len(pending) == 1
        assert pending[0].claim_token is None
        assert pending[0].attempts == 1

    asyncio.run(driver())


def test_cancelled_multi_kc_projection_compensates_partial_evidence(tmp_path: Path) -> None:
    class CancellingRecorder:
        def __init__(self) -> None:
            self.events: list[object] = []
            self.deleted_source_ids: list[str] = []

        async def record_event(self, event: object, *, trusted: bool) -> list[object]:
            assert trusted is True
            if self.events:
                raise asyncio.CancelledError
            self.events.append(event)
            return []

        async def delete_evidence(self, source_event_id: str) -> bool:
            self.deleted_source_ids.append(source_event_id)
            self.events.clear()
            return True

    async def driver() -> None:
        recorder = CancellingRecorder()
        chain = CanonicalAnswerEventChain(
            LearnerEventLedger(tmp_path / "multi-kc-events.json"),
            personalization_service_factory=lambda: recorder,
        )
        event, _ = chain.record_server_graded(
            user_id="u1",
            subject_id="math",
            question_id="q1",
            kc_ids=("kc1", "kc2"),
            attempt_id="multi-kc-cancel",
            is_correct=True,
            item_valid=True,
            attribution_reliable=True,
            derived=lambda _event: None,
        )

        await asyncio.gather(*chain._bg_tasks, return_exceptions=True)
        await asyncio.sleep(0)

        assert recorder.events == []
        assert recorder.deleted_source_ids == [event.event_id]
        pending = chain.ledger.pending_derived()
        assert len(pending) == 1
        assert pending[0].claim_token is None

    asyncio.run(driver())


def test_personalization_factory_failure_stays_retryable_without_masking_error(
    tmp_path: Path,
) -> None:
    def unavailable_service():
        raise RuntimeError("personalization unavailable")

    chain = CanonicalAnswerEventChain(
        LearnerEventLedger(tmp_path / "factory-failure-events.json"),
        personalization_service_factory=unavailable_service,
    )

    event, _ = chain.record_server_graded(
        user_id="u1",
        subject_id="math",
        question_id="q1",
        kc_ids=("kc1", "kc2"),
        attempt_id="factory-failure",
        is_correct=True,
        item_valid=True,
        attribution_reliable=True,
        derived=lambda _event: None,
    )

    pending = chain.ledger.pending_derived()
    assert len(pending) == 1
    assert pending[0].event_id == event.event_id
    assert pending[0].claim_token is None
    assert pending[0].last_error == "RuntimeError: personalization unavailable"


def test_memory_reconcile_failure_is_not_overwritten_as_completed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from traittutor.memory import runtime as memory_runtime

    service = PersonalizationService()
    monkeypatch.setattr(service, "_root", lambda: tmp_path / "learner")

    def fail_store(_owner: str):
        raise RuntimeError("memory store unavailable")

    monkeypatch.setattr(memory_runtime, "get_current_memory_store", fail_store)

    async def driver() -> None:
        assert service.enqueue_memory_reconcile()["state"] == "queued"
        for _ in range(10):
            await asyncio.sleep(0)
            if service.memory_reconcile_status().get("state") not in {"queued", "running"}:
                break

        assert service.memory_reconcile_status() == {
            "state": "failed",
            "last_completed_at": None,
            "imported": 0,
            "error": "RuntimeError",
        }

    asyncio.run(driver())


def _canonical_signal(signal_id: str) -> LearningSignal:
    """Build the signal shapes the canonical chain emits, old and new."""
    payload: dict[str, object] = {
        "event_type": "mastery_attempt",
        "concept": "kc1",
        "concept_id": "kc1",
        "module_id": "m1",
        "observation": "correct",
        "event_confidence": 1.0,
        "correct": True,
        "canonical_bkt_projection": True,
        "bkt_param_version": "v1-uncalibrated",
    }
    payload.update(
        {
            "canonical_mastery_probability": 0.42,
            "canonical_initial_mastery_probability": 0.2,
            "canonical_verified_observation_count": 1,
        }
    )
    return LearningSignal(
        signal_id=signal_id,
        kind="learner_event",
        subject_refs=[
            SubjectRef(
                subject_id="math",
                label="math",
                path=["math"],
                confidence=1.0,
                source="rule",
                confirmed=True,
            )
        ],
        payload=payload,
        evidence_refs=[],
        source="system",
        occurred_at="2026-08-10T00:00:00+00:00",
    )


def test_canonical_signal_fails_before_append_when_artifact_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Regression: a fail-closed parameter artifact must raise *before* the
    # signal is appended. Otherwise the idempotency gate swallows the later
    # retry and the profile never projects.
    monkeypatch.setenv(BKT_PARAMETERS_PATH_ENV, str(tmp_path / "missing.json"))
    service = PersonalizationService()
    monkeypatch.setattr(service, "_root", lambda: tmp_path / "learner")
    signal = _canonical_signal("evt-cfg-1")

    with pytest.raises(BKTParameterConfigurationError):
        asyncio.run(service.apply_signal(signal))

    # Operator fixes the config; the retry must now project for real.
    monkeypatch.delenv(BKT_PARAMETERS_PATH_ENV)
    monkeypatch.setenv("TRAITTUTOR_HOME", str(tmp_path))
    asyncio.run(service.apply_signal(signal))
    concepts = service.subject_profile("math").concept_signals
    assert len(concepts) == 1
    assert concepts[0].verified_observation_count == 1
    assert concepts[0].mastery_probability == pytest.approx(0.42)


def test_fail_closed_artifact_queues_projection_instead_of_500(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Regression: the immutable event is already durable when the fail-closed
    # artifact raises during projection. The grading caller must succeed and
    # the derivation must stay retryable, not surface as a 500.
    monkeypatch.setenv(BKT_PARAMETERS_PATH_ENV, str(tmp_path / "missing.json"))
    service = PersonalizationService()
    monkeypatch.setattr(service, "_root", lambda: tmp_path / "learner")
    chain = CanonicalAnswerEventChain(
        LearnerEventLedger(tmp_path / "events.json"),
        personalization_service_factory=lambda: service,
    )

    event, outcome = chain.record_server_graded(
        user_id="u1",
        subject_id="math",
        question_id="q1",
        kc_ids=("kc1",),
        attempt_id="attempt-1",
        is_correct=True,
        item_valid=True,
        attribution_reliable=True,
        derived=lambda _event: None,
    )

    assert outcome == "applied"
    assert chain.ledger.get(event.event_id) is not None
    assert service.subject_profile("math").concept_signals == []

    # Operator fixes the config; the queued retry now projects.
    monkeypatch.delenv(BKT_PARAMETERS_PATH_ENV)
    monkeypatch.setenv("TRAITTUTOR_HOME", str(tmp_path))
    chain.retry_personalization()
    concepts = service.subject_profile("math").concept_signals
    assert len(concepts) == 1
    assert concepts[0].verified_observation_count == 1
