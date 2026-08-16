"""Canonical answer-event chain."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
import hashlib
import logging
from pathlib import Path
import time
from typing import Any, Literal

from traittutor.learning_model import (
    BKTParamSet,
    KnowledgeStateKey,
    KnowledgeStateStore,
    LearnerEvent,
    LearnerEventLedger,
    get_active_bkt_params,
    rebuild_knowledge_states,
)
from traittutor.learning_model.events import is_strong_evidence
from traittutor.personalization.models import LearnerEvent as PersonalizationEvent
from traittutor.personalization.models import SubjectRef
from traittutor.personalization.service import get_personalization_service
from traittutor.services.path_service import get_path_service

LEARNING_DERIVED_OPERATION = "learning-derived"
PERSONALIZATION_BKT_OPERATION = "personalization-bkt"
PERSONALIZATION_CLAIM_LEASE_SECONDS = 120.0
logger = logging.getLogger(__name__)


def stable_answer_identity(*, user_id: str, attempt_id: str) -> tuple[str, str]:
    """Bind idempotency to one submission attempt, never its answer content."""
    if not attempt_id.strip():
        raise ValueError("attempt_id is required")
    material = "\x1f".join((user_id, attempt_id)).encode()
    digest = hashlib.sha256(material).hexdigest()
    return f"answer-{digest[:48]}", f"answer-submit:{digest}"


class CanonicalAnswerEventChain:
    """Persist an answer first, then run named idempotent projections."""

    def __init__(
        self,
        ledger: LearnerEventLedger | None = None,
        *,
        path: Path | None = None,
        personalization_service_factory: Callable[[], Any] = get_personalization_service,
        personalization_claim_lease_seconds: float = PERSONALIZATION_CLAIM_LEASE_SECONDS,
    ):
        if ledger is not None and path is not None:
            raise ValueError("pass ledger or path, not both")
        # When no explicit ``path`` is given (the production default), thread
        # the workspace path service so the ledger resolves to the canonical
        # unified database where the Phase 4 migration landed the historical
        # events. An explicit ``path`` (tests) keeps its isolated location.
        path_service = get_path_service() if path is None else None
        ledger_path = path or (
            get_path_service().get_workspace_dir() / "learning_model" / "learner_events.json"
        )
        # A fresh ledger has ``len == 0`` and is therefore falsey; test identity
        # explicitly so an injected durable ledger is never silently replaced.
        self.ledger = (
            ledger
            if ledger is not None
            else LearnerEventLedger(ledger_path, path_service=path_service)
        )
        self._personalization_service_factory = personalization_service_factory
        if personalization_claim_lease_seconds <= 0:
            raise ValueError("personalization claim lease must be positive")
        self._personalization_claim_lease_seconds = personalization_claim_lease_seconds
        # Strong references for fire-and-forget BKT projections scheduled on a
        # running loop. Without one, CPython may collect the task before the
        # scheduler runs it (the documented asyncio task-GC footgun), so the
        # done_callback never fires and the projection stays pending forever.
        self._bg_tasks: set[asyncio.Task[None]] = set()
        self._bg_personalization_event_ids: set[str] = set()

    def record_server_graded(
        self,
        *,
        user_id: str,
        subject_id: str,
        question_id: str,
        kc_ids: Iterable[str],
        is_correct: bool,
        item_valid: bool,
        attribution_reliable: bool,
        derived: Callable[[LearnerEvent], Any],
        attempt_id: str,
        event_id: str | None = None,
        idempotency_key: str | None = None,
        surface_type: Literal["quiz", "practice", "review"] = "practice",
        module_id: str | None = None,
        learning_path_id: str | None = None,
        error_tag: str | None = None,
        defer_derived: bool = False,
    ) -> tuple[LearnerEvent, str]:
        """Record trusted deterministic grading and apply its projection once."""
        stable_event_id, stable_key = stable_answer_identity(
            user_id=user_id,
            attempt_id=attempt_id,
        )
        now = datetime.now(UTC).isoformat()
        kc_tuple = tuple(kc_ids)
        reliable = item_valid and attribution_reliable and bool(subject_id) and bool(kc_tuple)
        event = LearnerEvent(
            event_id=event_id or stable_event_id,
            idempotency_key=idempotency_key or stable_key,
            user_id=user_id,
            subject_id=subject_id or None,
            kc_ids=kc_tuple,
            surface_type=surface_type,
            answer_correct=is_correct,
            evidence_strength="strong" if reliable else "none",
            attribution_status="reliable" if reliable else "attribution_pending",
            page_id=question_id,
            module_id=module_id,
            learning_path_id=learning_path_id,
            item_id=question_id,
            error_tag=error_tag,
            created_at=now,
        )
        operations = (LEARNING_DERIVED_OPERATION, PERSONALIZATION_BKT_OPERATION) if reliable else ()
        appended = self.ledger.append(event, derived_operations=operations)
        if appended == "duplicate":
            event = (
                self.ledger.event_for_identity(
                    event_id=event.event_id, idempotency_key=event.idempotency_key
                )
                or event
            )
        if not is_strong_evidence(event):
            return event, "already_applied"
        if defer_derived:
            return event, "queued"
        outcome = self.ledger.apply_derived(
            event.event_id,
            LEARNING_DERIVED_OPERATION,
            derived,
            now=now,
        )
        self.project_personalization(event.event_id, now=now)
        return event, outcome

    def project_personalization(self, event_id: str, *, now: str) -> str:
        """Project strong evidence to the one live ConceptSignal BKT store."""
        event = self.ledger.get(event_id)
        if event is None:
            raise KeyError(event_id)
        # A late worker must not re-project evidence that was voided after the
        # durable event write.  Already-written profiles are retracted by the
        # correction workflow; this guard keeps queued/replayed work honest.
        if not self.ledger.is_effective(event_id) or not is_strong_evidence(event):
            return "already_applied"
        # Keep the local guard for cheap duplicate suppression, but correctness
        # comes from the durable claim below: separate workers cannot schedule
        # the same live projection while its lease remains valid.
        if event_id in self._bg_personalization_event_ids:
            return "queued"
        claim = self.ledger.claim_derived(
            event_id,
            PERSONALIZATION_BKT_OPERATION,
            now=now,
            lease_seconds=self._personalization_claim_lease_seconds,
        )
        if claim is None:
            pending = {
                item.event_id
                for item in self.ledger.pending_derived()
                if item.operation == PERSONALIZATION_BKT_OPERATION
            }
            return "queued" if event_id in pending else "already_applied"
        try:
            personalization_events = self._to_personalization_events(event)
        except Exception as exc:
            # The immutable event is already persisted. A fail-closed parameter
            # artifact (or a transient rebuild race) must not turn the grading
            # call into a 500: mark the derivation failed so the caller
            # succeeds and a later retry can project once the config is fixed.
            self.ledger.mark_derived_failed(
                event_id,
                PERSONALIZATION_BKT_OPERATION,
                exc,
                now=now,
                claim_token=claim.token,
            )
            return "queued"

        async def project() -> None:
            owner_task = asyncio.current_task()
            loop = asyncio.get_running_loop()
            heartbeat_interval = max(
                0.01,
                min(self._personalization_claim_lease_seconds / 3.0, 30.0),
            )
            heartbeat: asyncio.TimerHandle | None = None
            partial_failure = False
            personalization_service: Any | None = None

            def renew_claim() -> None:
                nonlocal heartbeat
                try:
                    renewed = self.ledger.renew_derived_claim(
                        event_id,
                        PERSONALIZATION_BKT_OPERATION,
                        claim_token=claim.token,
                        now=datetime.now(UTC).isoformat(),
                        lease_seconds=self._personalization_claim_lease_seconds,
                    )
                except Exception:
                    logger.exception(
                        "failed to renew derived claim for event %s",
                        event_id,
                    )
                    renewed = None
                if renewed is None:
                    # The token was fenced or expired. Stop the expensive
                    # callback; downstream event-id idempotency remains the
                    # final defence if it committed concurrently.
                    if owner_task is not None:
                        owner_task.cancel()
                    return
                heartbeat = loop.call_later(heartbeat_interval, renew_claim)

            heartbeat = loop.call_later(heartbeat_interval, renew_claim)
            try:
                personalization_service = self._personalization_service_factory()
                for personalization_event in personalization_events:
                    await personalization_service.record_event(
                        personalization_event,
                        trusted=True,
                    )
                # A correction can land while an in-flight projection owns its
                # lease.  Retract the just-written signal before completion so
                # no read path keeps superseded evidence after the ledger has
                # declared it ineffective.
                if not self.ledger.is_effective(event_id):
                    await personalization_service.delete_evidence(event_id)
            except BaseException:
                # Exceptions and task cancellation can both arrive after an
                # earlier KC was committed. Compensate the whole source event
                # before propagating so no partial multi-KC evidence is visible.
                partial_failure = True
                raise
            finally:
                if heartbeat is not None:
                    heartbeat.cancel()
                if partial_failure and personalization_service is not None:
                    # Compensation: remove any successfully written KCs to keep
                    # the store consistent and allow clean retry.
                    try:
                        await personalization_service.delete_evidence(event_id)
                    except Exception:
                        logger.exception(
                            "failed to clean up partial evidence for event %s",
                            event_id,
                        )

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            try:
                asyncio.run(project())
            except asyncio.CancelledError as exc:
                self.ledger.mark_derived_failed(
                    event_id,
                    PERSONALIZATION_BKT_OPERATION,
                    exc,
                    now=now,
                    claim_token=claim.token,
                )
                return "queued"
            except Exception as exc:
                self.ledger.mark_derived_failed(
                    event_id,
                    PERSONALIZATION_BKT_OPERATION,
                    exc,
                    now=now,
                    claim_token=claim.token,
                )
                return "queued"
            return self.ledger.mark_derived_applied(
                event_id,
                PERSONALIZATION_BKT_OPERATION,
                claim_token=claim.token,
            )

        task = loop.create_task(project())
        self._bg_tasks.add(task)  # hold a strong ref until completion
        self._bg_personalization_event_ids.add(event_id)

        def finish(done: asyncio.Task[None]) -> None:
            self._bg_tasks.discard(done)
            self._bg_personalization_event_ids.discard(event_id)
            completed_at = datetime.now(UTC).isoformat()
            try:
                done.result()
            except asyncio.CancelledError as exc:
                # A worker shutdown must release its durable claim promptly;
                # otherwise healthy workers cannot retry until the full lease
                # expires. The immutable source event remains intact.
                self.ledger.mark_derived_failed(
                    event_id,
                    PERSONALIZATION_BKT_OPERATION,
                    exc,
                    now=completed_at,
                    claim_token=claim.token,
                )
            except Exception as exc:
                self.ledger.mark_derived_failed(
                    event_id,
                    PERSONALIZATION_BKT_OPERATION,
                    exc,
                    now=completed_at,
                    claim_token=claim.token,
                )
            else:
                self.ledger.mark_derived_applied(
                    event_id,
                    PERSONALIZATION_BKT_OPERATION,
                    claim_token=claim.token,
                )

        task.add_done_callback(finish)
        return "queued"

    def retry_personalization(self, *, backoff_seconds: float = 1.0) -> None:
        """Resume BKT projections left pending by a prior failure or shutdown.

        Args:
            backoff_seconds: Base delay between retries to avoid CPU spikes.
                           Uses exponential backoff: delay * 2^(retry_count).
        """
        now = datetime.now(UTC).isoformat()
        pending = [
            item
            for item in self.ledger.pending_derived()
            if item.operation == PERSONALIZATION_BKT_OPERATION
        ]
        for index, item in enumerate(pending):
            if index > 0:
                # Sync context: the un-awaited ``asyncio.sleep`` previously
                # returned an unused coroutine, so the backoff never applied.
                delay = min(backoff_seconds * (2 ** (index - 1)), 60.0)
                time.sleep(delay)
            self.project_personalization(item.event_id, now=now)

    @staticmethod
    def _kc_projection_event_id(event_id: str, kc_id: str) -> str:
        """Return a stable bounded signal ID for an additional KC projection."""
        digest = hashlib.sha256(f"{event_id}\x1f{kc_id}".encode()).hexdigest()
        return f"bkt-{digest[:64]}"

    def _to_personalization_events(
        self,
        event: LearnerEvent,
    ) -> tuple[PersonalizationEvent, ...]:
        """Project every attributed KC while retaining one source-event receipt."""
        if event.subject_id is None or not event.kc_ids:
            raise ValueError("strong evidence requires subject and KC attribution")
        subject = SubjectRef(
            subject_id=event.subject_id,
            label=event.subject_id,
            path=[event.subject_id],
            confidence=1.0,
            source="rule",
            confirmed=True,
        )
        active_params = get_active_bkt_params()
        canonical_states = self.rebuild_bkt(params=active_params)
        projections: list[PersonalizationEvent] = []
        for index, kc_id in enumerate(event.kc_ids):
            canonical_state = canonical_states.get(
                KnowledgeStateKey(
                    user_id=event.user_id,
                    subject_id=event.subject_id,
                    kc_id=kc_id,
                )
            )
            if canonical_state is None:
                raise ValueError("strong evidence did not produce a canonical BKT state")
            projections.append(
                PersonalizationEvent(
                    # Preserve the historical ID for the first KC. Additional
                    # cells need their own stable learner-audit identities so
                    # retries can finish a partially projected multi-KC event.
                    event_id=(
                        event.event_id
                        if index == 0
                        else self._kc_projection_event_id(event.event_id, kc_id)
                    ),
                    event_type="mastery_attempt",
                    subject=subject,
                    concept_id=kc_id,
                    concept_label=kc_id,
                    module_id=event.module_id,
                    observation="correct" if event.answer_correct else "incorrect",
                    confidence=1.0,
                    evidence_refs=[f"learner-event:{event.event_id}"],
                    # This marker distinguishes immutable, server-graded
                    # canonical evidence from the legacy personalization
                    # stream and groups per-KC signals for source retraction.
                    payload={
                        "bkt_param_version": active_params.version,
                        "canonical_bkt_projection": True,
                        "canonical_source_event_id": event.event_id,
                        "canonical_mastery_probability": canonical_state.mastery_probability,
                        "canonical_initial_mastery_probability": (
                            canonical_state.initial_mastery_probability
                        ),
                        "canonical_verified_observation_count": (
                            canonical_state.verified_observation_count
                        ),
                    },
                    occurred_at=event.created_at,
                )
            )
        return tuple(projections)

    def _to_personalization_event(self, event: LearnerEvent) -> PersonalizationEvent:
        """Compatibility adapter for single-KC callers and contract tests."""
        return self._to_personalization_events(event)[0]

    def rebuild_bkt(self, params: BKTParamSet | None = None) -> KnowledgeStateStore:
        """Rebuild the canonical per-KC BKT read model from the durable stream."""
        return rebuild_knowledge_states(self.ledger.effective_events(), params=params)

    def record_ungraded_submission(
        self,
        *,
        user_id: str,
        question_id: str,
        attempt_id: str,
        subject_id: str = "",
    ) -> LearnerEvent:
        """Record AI/client judged input as pending attribution, never BKT evidence."""
        event_id, key = stable_answer_identity(
            user_id=user_id,
            attempt_id=attempt_id,
        )
        event = LearnerEvent(
            event_id=event_id,
            idempotency_key=key,
            user_id=user_id,
            subject_id=subject_id or None,
            kc_ids=(),
            surface_type="quiz",
            answer_correct=None,
            evidence_strength="none",
            attribution_status="attribution_pending",
            item_id=question_id,
            page_id=question_id,
            created_at=datetime.now(UTC).isoformat(),
        )
        self.ledger.append(event)
        return event
