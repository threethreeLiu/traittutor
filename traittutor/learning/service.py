from __future__ import annotations

from datetime import UTC, datetime
from functools import partial
import logging
import time
from typing import TYPE_CHECKING
import uuid

from traittutor.learning.event_chain import (
    LEARNING_DERIVED_OPERATION,
    CanonicalAnswerEventChain,
)
from traittutor.learning.grading import classify_error, grade_answer
from traittutor.learning.models import (
    ErrorRecord,
    ErrorRecordStatus,
    ErrorType,
    LearningModule,
    LearningProgress,
    LearningStage,
    PendingQuestion,
    QuizAttempt,
    RetryAttempt,
)
from traittutor.learning.storage import LearningStore
from traittutor.learning_model import LearnerEvent
from traittutor.learning_model.events import is_strong_evidence

if TYPE_CHECKING:
    from traittutor.learning.scheduler import SpacedRepetitionScheduler
    from traittutor.learning_model.mastery_read_view import MasteryReadView

logger = logging.getLogger(__name__)

RECOVERY_FAILURE_THRESHOLD = 2
QUIZ_ATTEMPT_PROJECTION_LIMIT = 256
ERROR_DETAIL_PROJECTION_LIMIT = 32


class LearningService:
    def __init__(
        self,
        store: LearningStore | None = None,
        *,
        event_chain: CanonicalAnswerEventChain | None = None,
        resume_canonical_derivations: bool = True,
    ) -> None:
        self._store = store or LearningStore()
        self._event_chain = event_chain
        self._event_chain = self._event_chain or CanonicalAnswerEventChain()
        if resume_canonical_derivations:
            self._retry_canonical_derivations()

    def get_or_create(self, book_id: str) -> LearningProgress:
        existing = self._store.load(book_id)
        if existing is not None:
            return existing
        progress = LearningProgress(book_id=book_id)
        self._store.save(progress)  # persist immediately to prevent race
        return progress

    def mastery_read_view(
        self,
        progress: LearningProgress,
        *,
        user_id: str | None = None,
    ) -> MasteryReadView | None:
        """Bind policy reads to the path's one authoritative subject.

        New paths persist ``subject_id`` from their first reliably attributed
        canonical event. For pre-migration paths, one unique strong-event
        subject for the same user + learning path is an equivalent durable
        source. Zero or multiple subjects fail closed instead of guessing from
        a book/module label.
        """
        if self._event_chain is None:
            raise RuntimeError("canonical answer event chain is unavailable")
        from traittutor.learning_model.events import is_strong_evidence
        from traittutor.learning_model.mastery_read_view import MasteryReadView
        from traittutor.multi_user.context import get_current_user

        owner_id = (user_id or get_current_user().id).strip()
        if not owner_id:
            return None
        event_subjects = {
            event.subject_id
            for event in self._event_chain.ledger.events_for(user_id=owner_id)
            if event.learning_path_id == progress.book_id
            and event.subject_id
            and is_strong_evidence(event)
        }
        subject_id = progress.subject_id.strip()
        if subject_id:
            if event_subjects and event_subjects != {subject_id}:
                return None
        elif len(event_subjects) == 1:
            subject_id = next(iter(event_subjects))
        else:
            return None
        return MasteryReadView.from_ledger(
            self._event_chain.ledger,
            user_id=owner_id,
            subject_id=subject_id,
        )

    def init_modules(self, progress: LearningProgress, modules: list[LearningModule]) -> None:
        """Initialize the runnable module set (replace semantics)."""
        self.replace_modules(progress, modules)

    def replace_modules(self, progress: LearningProgress, modules: list[LearningModule]) -> None:
        """Replace all modules and clean stale KP state."""
        new_kp_ids = {kp.id for m in modules for kp in m.knowledge_points}

        # Clean stale KP state
        for key in list(progress.knowledge_types.keys()):
            if key not in new_kp_ids:
                del progress.knowledge_types[key]
        for key in list(progress.repetition_states.keys()):
            if key not in new_kp_ids:
                del progress.repetition_states[key]
        progress.error_records = [
            r for r in progress.error_records if r.knowledge_point_id in new_kp_ids
        ]
        progress.feynman_retries = {
            k: v for k, v in progress.feynman_retries.items() if k in new_kp_ids
        }
        progress.feynman_explanations = {
            k: v for k, v in progress.feynman_explanations.items() if k in new_kp_ids
        }
        progress.consecutive_failures_by_kc = {
            k: v for k, v in progress.consecutive_failures_by_kc.items() if k in new_kp_ids
        }
        progress.deferred_knowledge_points = {
            k: v for k, v in progress.deferred_knowledge_points.items() if k in new_kp_ids
        }
        progress.review_queue = [
            t for t in progress.review_queue if t.knowledge_point_id in new_kp_ids
        ]
        # Clear global stage failure records — different modules should not share failure counts
        progress.stage_failure_counts = {}
        progress.stage_failure_notes = {}

        # Set new modules
        progress.modules = list(modules)
        for mod in modules:
            for kp in mod.knowledge_points:
                progress.knowledge_types[kp.id] = kp.type

    def advance_stage(self, progress: LearningProgress, next_stage: LearningStage) -> None:
        progress.current_stage = next_stage
        progress.updated_at = time.time()

    def switch_module(self, progress: LearningProgress, module_id: str) -> bool:
        """Point the session at ``module_id`` and reset it to that module's
        first teaching stage (EXPLAIN). Mutates ``progress`` in place and returns
        whether the module exists. The caller is responsible for persisting
        (``save``) — typically *after* cancelling any in-flight turn so the
        turn's teardown cannot overwrite the switch with stale progress.
        """
        found = any(m.id == module_id for m in progress.modules)
        if found:
            progress.current_module_id = module_id
            progress.current_kp_index = 0
            progress.current_stage = LearningStage.EXPLAIN
            progress.updated_at = time.time()
        return found

    def record_quiz_attempt(self, progress: LearningProgress, attempt: QuizAttempt) -> bool:
        # Replay guard reads the unbounded seen set, never the trimmed
        # quiz_attempts display projection (invariant #1: same event_id must
        # never re-score).
        if attempt.event_id and attempt.event_id in progress.seen_quiz_event_ids:
            return False
        observed_at = attempt.timestamp
        if not attempt.is_correct and attempt.error_type is not None:
            # Find existing error record for this question + knowledge point.
            existing = None
            for rec in progress.error_records:
                if (
                    rec.question_id == attempt.question_id
                    and rec.knowledge_point_id == attempt.knowledge_point_id
                ):
                    existing = rec
                    break

            if existing is not None:
                existing.total_retry_count = (
                    max(
                        existing.total_retry_count,
                        len(existing.retry_history),
                    )
                    + 1
                )
                existing.retry_history.append(
                    RetryAttempt(
                        timestamp=observed_at,
                        is_correct=False,
                        attempt_number=existing.total_retry_count,
                        event_id=attempt.event_id,
                    )
                )
                existing.retry_history = existing.retry_history[-ERROR_DETAIL_PROJECTION_LIMIT:]
                if existing.status == ErrorRecordStatus.REPAIRED:
                    existing.status = ErrorRecordStatus.RELAPSED
                    existing.relapsed_at = observed_at
                existing.last_seen_at = observed_at
                if attempt.event_id and attempt.event_id not in existing.source_event_ids:
                    existing.total_source_event_count = (
                        max(
                            existing.total_source_event_count,
                            len(existing.source_event_ids),
                        )
                        + 1
                    )
                    existing.source_event_ids.append(attempt.event_id)
                    existing.source_event_ids = existing.source_event_ids[
                        -ERROR_DETAIL_PROJECTION_LIMIT:
                    ]
            else:
                record = ErrorRecord(
                    id=uuid.uuid4().hex,
                    question_id=attempt.question_id,
                    knowledge_point_id=attempt.knowledge_point_id,
                    module_id=attempt.module_id,
                    error_type=attempt.error_type,
                    self_attribution=attempt.self_attribution,
                    status=ErrorRecordStatus.OPEN,
                    source_event_ids=[attempt.event_id] if attempt.event_id else [],
                    total_source_event_count=1 if attempt.event_id else 0,
                    created_at=observed_at,
                    last_seen_at=observed_at,
                )
                progress.error_records.append(record)

        elif attempt.is_correct:
            # Repair, never delete: the original error and retry evidence stay
            # auditable, and a later wrong answer can mark the same record relapsed.
            for rec in progress.error_records:
                if (
                    rec.question_id == attempt.question_id
                    and rec.knowledge_point_id == attempt.knowledge_point_id
                    and rec.status in (ErrorRecordStatus.OPEN, ErrorRecordStatus.RELAPSED)
                ):
                    rec.total_retry_count = (
                        max(
                            rec.total_retry_count,
                            len(rec.retry_history),
                        )
                        + 1
                    )
                    rec.retry_history.append(
                        RetryAttempt(
                            timestamp=observed_at,
                            is_correct=True,
                            attempt_number=rec.total_retry_count,
                            event_id=attempt.event_id,
                        )
                    )
                    rec.retry_history = rec.retry_history[-ERROR_DETAIL_PROJECTION_LIMIT:]
                    rec.status = ErrorRecordStatus.REPAIRED
                    rec.repaired_at = observed_at
                    rec.last_seen_at = observed_at
                    if attempt.event_id and attempt.event_id not in rec.source_event_ids:
                        rec.total_source_event_count = (
                            max(
                                rec.total_source_event_count,
                                len(rec.source_event_ids),
                            )
                            + 1
                        )
                        rec.source_event_ids.append(attempt.event_id)
                        rec.source_event_ids = rec.source_event_ids[-ERROR_DETAIL_PROJECTION_LIMIT:]
                    break

        progress.quiz_attempts.append(attempt)
        progress.quiz_attempts = progress.quiz_attempts[-QUIZ_ATTEMPT_PROJECTION_LIMIT:]
        if attempt.event_id:
            progress.seen_quiz_event_ids.append(attempt.event_id)
        progress.updated_at = time.time()
        return True

    def record_recovery_outcome(
        self,
        progress: LearningProgress,
        kp_id: str,
        *,
        succeeded: bool,
    ) -> bool:
        """Project one trusted outcome into the non-evidentiary recovery state.

        Returns whether this outcome newly deferred ``kp_id``.  The caller is
        responsible for persisting so canonical event derivation can save this
        atomically with the attempt/error/review projections.
        """
        progress.objective_attempt_sequence += 1
        sequence = progress.objective_attempt_sequence

        # A trusted attempt on any different KC is the release condition.  It
        # does not matter whether that attempt passed, and it never rewrites
        # the deferred KC's evidence.
        for deferred_kp in list(progress.deferred_knowledge_points):
            if deferred_kp != kp_id:
                del progress.deferred_knowledge_points[deferred_kp]
                progress.consecutive_failures_by_kc[deferred_kp] = 0

        if succeeded:
            progress.consecutive_failures_by_kc[kp_id] = 0
            progress.deferred_knowledge_points.pop(kp_id, None)
            return False

        failures = progress.consecutive_failures_by_kc.get(kp_id, 0) + 1
        progress.consecutive_failures_by_kc[kp_id] = failures
        if failures < RECOVERY_FAILURE_THRESHOLD:
            return False
        progress.deferred_knowledge_points[kp_id] = sequence
        progress.consecutive_failures_by_kc[kp_id] = 0
        return True

    def resume_deferred_objective(self, progress: LearningProgress, kp_id: str) -> bool:
        """Explicitly release one recovery pause without changing mastery."""
        if kp_id not in progress.deferred_knowledge_points:
            return False
        del progress.deferred_knowledge_points[kp_id]
        progress.consecutive_failures_by_kc[kp_id] = 0
        progress.updated_at = time.time()
        self.save(progress)
        return True

    def grade_and_record(
        self,
        progress: LearningProgress,
        *,
        question_id: str,
        knowledge_point_id: str,
        module_id: str,
        user_answer: str,
        expected_answer: str,
        question_type: str = "short",
        self_attribution: str = "",
        scheduler: SpacedRepetitionScheduler | None = None,
        user_id: str | None = None,
        subject_id: str | None = None,
        attempt_id: str | None = None,
        event_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> bool:
        """Grade one answer and fold it through the full post-answer pipeline.

        record attempt -> recompute mastery -> advance the spaced-repetition
        state -> rebuild the review queue -> persist. This is the single source
        of truth for what happens when a student answers, shared by every
        interactive stage. Grading is fail-closed: with no stored expected
        answer the attempt is recorded wrong, never right.
        """
        is_correct = bool(expected_answer) and grade_answer(
            user_answer, expected_answer, question_type
        )
        from traittutor.multi_user.context import get_current_user

        chain = self._event_chain or CanonicalAnswerEventChain()
        pending = progress.pending_question
        item_valid = bool(
            expected_answer
            and question_id
            and pending is not None
            and pending.question_id == question_id
            and pending.knowledge_point_id == knowledge_point_id
            and (not pending.module_id or pending.module_id == module_id)
        )
        attribution_reliable = any(
            module.id == module_id
            and any(kp.id == knowledge_point_id for kp in module.knowledge_points)
            for module in progress.modules
        )
        resolved_attempt_id = (
            attempt_id
            or event_id
            or (
                pending.attempt_id
                if pending is not None and pending.question_id == question_id
                else None
            )
        )
        if not resolved_attempt_id:
            raise ValueError("attempt_id is required for canonical grading")
        error_type = None if is_correct else classify_error(user_answer)
        effective_subject_id = (subject_id or "").strip()
        same_path_subject = not progress.subject_id or progress.subject_id == effective_subject_id
        chain.record_server_graded(
            user_id=user_id or get_current_user().id,
            subject_id=effective_subject_id,
            question_id=question_id,
            kc_ids=(knowledge_point_id,) if knowledge_point_id else (),
            is_correct=is_correct,
            item_valid=item_valid,
            attribution_reliable=attribution_reliable and same_path_subject,
            attempt_id=resolved_attempt_id,
            derived=lambda recorded: self._project_learning_progress(
                progress,
                recorded,
                scheduler=scheduler,
                self_attribution=self_attribution,
            ),
            event_id=event_id,
            idempotency_key=idempotency_key,
            module_id=module_id or None,
            learning_path_id=progress.book_id,
            error_tag=error_type.value if error_type else None,
        )
        return is_correct

    def _project_learning_progress(
        self,
        progress: LearningProgress,
        event: LearnerEvent,
        *,
        scheduler: SpacedRepetitionScheduler | None,
        self_attribution: str = "",
    ) -> None:
        """Idempotently derive attempt/error/review state from one event."""
        candidate = progress.model_copy(deep=True)
        try:
            timestamp = datetime.fromisoformat(event.created_at).timestamp()
        except ValueError:
            timestamp = time.time()
        error_type = ErrorType(event.error_tag) if event.error_tag else None
        inserted = self.record_quiz_attempt(
            candidate,
            QuizAttempt(
                question_id=event.item_id or event.page_id or event.event_id,
                knowledge_point_id=event.kc_ids[0],
                module_id=event.module_id or "",
                is_correct=bool(event.answer_correct),
                user_answer=None,
                self_attribution=self_attribution,
                error_type=error_type,
                timestamp=timestamp,
                event_id=event.event_id,
                idempotency_key=event.idempotency_key,
            ),
        )
        if not inserted:
            return
        if event.subject_id:
            if candidate.subject_id and candidate.subject_id != event.subject_id:
                raise ValueError("learning path subject does not match canonical event")
            candidate.subject_id = event.subject_id
        kc_id = event.kc_ids[0]
        self.record_recovery_outcome(
            candidate,
            kc_id,
            succeeded=bool(event.answer_correct),
        )
        candidate.verified_observation_counts[kc_id] = (
            candidate.verified_observation_counts.get(kc_id, 0) + 1
        )
        candidate.mastery_intervals[kc_id] = (0.0, 1.0)
        kp_type = candidate.knowledge_types.get(kc_id)
        if kp_type is not None and scheduler is not None:
            state = candidate.repetition_states.get(kc_id) or scheduler.get_initial_state(kp_type)
            candidate.repetition_states[kc_id] = state
            scheduler.schedule_next(state, kp_type, bool(event.answer_correct))
            candidate.review_queue = scheduler.build_review_queue(candidate)
        self.save(candidate)
        for field_name in type(progress).model_fields:
            setattr(progress, field_name, getattr(candidate, field_name))

    def project_existing_canonical_event(self, event: LearnerEvent) -> bool:
        """Project one strong event into its already-owned learning path.

        Courseware and Learning Pack surfaces do not own a second progress
        model.  They may therefore derive ErrorRecord and review state only
        when their server-owned path ID resolves to an existing
        ``LearningProgress`` whose module graph contains the canonical KC.
        A missing path, subject, or KC is intentionally a no-op here: the
        immutable event remains attribution-pending/strong according to the
        event-chain contract, but we never guess a path from a pack title or
        create an unbound progress document.
        """
        if (
            not is_strong_evidence(event)
            or not event.learning_path_id
            or not event.subject_id
            or not event.kc_ids
        ):
            return False
        from traittutor.multi_user.context import get_current_user

        if get_current_user().id != event.user_id:
            return False
        progress = self._store.load(event.learning_path_id)
        if progress is None:
            return False
        kc_id = event.kc_ids[0]
        knowledge_point = next(
            (
                candidate
                for module in progress.modules
                for candidate in module.knowledge_points
                if candidate.id == kc_id
            ),
            None,
        )
        if knowledge_point is None:
            return False
        if progress.subject_id and progress.subject_id != event.subject_id:
            return False
        # Old progress documents can carry modules without the additive
        # knowledge_types map.  The module's typed KC is the durable source,
        # so recover it before building the review queue rather than silently
        # writing an attempt with no schedulable review state.
        progress.knowledge_types.setdefault(kc_id, knowledge_point.type)
        from traittutor.learning.scheduler import SpacedRepetitionScheduler

        self._project_learning_progress(
            progress,
            event,
            scheduler=SpacedRepetitionScheduler(),
        )
        return True

    def has_existing_canonical_target(
        self,
        *,
        user_id: str,
        subject_id: str,
        kc_id: str,
        learning_path_id: str,
    ) -> bool:
        """Return whether a server-owned answer can target one known path.

        A quiz surface may have a server-held answer key and a subject/KC
        label without owning a learning path.  It must not turn a session ID,
        pack title, or browser value into a path by inference.  This preflight
        therefore accepts only an explicit path association and the exact
        module graph already persisted for the authenticated user.  The real
        mutation still goes through :meth:`project_existing_canonical_event`
        after the immutable event is written.
        """
        if not user_id or not subject_id or not kc_id or not learning_path_id:
            return False
        from traittutor.multi_user.context import get_current_user

        if get_current_user().id != user_id:
            return False
        progress = self._store.load(learning_path_id)
        if progress is None:
            return False
        if progress.subject_id and progress.subject_id != subject_id:
            return False
        return any(
            candidate.id == kc_id
            for module in progress.modules
            for candidate in module.knowledge_points
        )

    def _retry_canonical_derivations(self) -> None:
        """Resume queued progress and BKT projections after service load."""
        from traittutor.learning.scheduler import SpacedRepetitionScheduler

        if self._event_chain is None:
            return
        scheduler = SpacedRepetitionScheduler()

        def project(recorded: LearnerEvent, target: LearningProgress) -> None:
            self._project_learning_progress(target, recorded, scheduler=scheduler)

        for item in self._event_chain.ledger.pending_derived():
            if item.operation != LEARNING_DERIVED_OPERATION:
                continue
            event = self._event_chain.ledger.get(item.event_id)
            if event is None or not event.learning_path_id:
                continue
            progress = self._store.load(event.learning_path_id)
            if progress is None:
                continue
            self._event_chain.ledger.apply_derived(
                event.event_id,
                LEARNING_DERIVED_OPERATION,
                partial(project, target=progress),
                now=datetime.now(UTC).isoformat(),
            )
        self._event_chain.retry_personalization()

    # ── Loop-driven tutoring helpers ─────────────────────────────────────

    def set_pending_question(self, progress: LearningProgress, pending: PendingQuestion) -> None:
        """Store the question the tutor just posed so its expected answer can
        be graded deterministically on a later turn (never via the model)."""
        progress.pending_question = pending
        progress.updated_at = time.time()
        self.save(progress)

    def clear_pending_question(self, progress: LearningProgress) -> None:
        progress.pending_question = None
        progress.updated_at = time.time()
        self.save(progress)

    def record_qualitative(
        self,
        progress: LearningProgress,
        kp_id: str,
        *,
        passed: bool,
        evidence: str = "",
    ) -> None:
        """Record the qualitative (CONCEPT / DESIGN) gate outcome.

        The boolean is the gate of record and is never mixed into BKT.
        """
        progress.qualitative_mastery[kp_id] = bool(passed)
        self.record_recovery_outcome(progress, kp_id, succeeded=bool(passed))
        if evidence:
            progress.feynman_explanations[kp_id] = evidence
        progress.updated_at = time.time()
        self.save(progress)

    def list_progress(self) -> dict:
        """Return summary of all book progress with per-book error info."""
        logger = logging.getLogger(__name__)

        book_ids = self._store.list_all()
        summaries = []
        errors = []
        for bid in book_ids:
            try:
                progress = self._store.load(bid)
                if progress is None:
                    continue
                # The summary uses the same policy/read-view as progression.
                # An uncalibrated or incomplete canonical set stays unknown.
                from traittutor.learning.policy import map_summary

                mastery_read_view = self.mastery_read_view(progress)
                mastery_map = map_summary(
                    progress,
                    mastery_read_view=mastery_read_view,
                )
                current_kp_ids = {
                    kp.id for module in progress.modules for kp in module.knowledge_points
                }
                total_kps = len(current_kp_ids)
                # Derive display name from first module, fall back to book_id
                display_name = ""
                if progress.modules:
                    display_name = progress.modules[0].name or ""
                summaries.append(
                    {
                        "book_id": progress.book_id,
                        "name": display_name or progress.book_id,
                        "modules_count": len(progress.modules),
                        "kp_count": total_kps,
                        "current_stage": progress.current_stage.value
                        if progress.current_stage
                        else "",
                        "evidence_state_counts": {
                            state: sum(
                                kp.get("evidence_state") == state
                                for module in mastery_map["modules"]
                                for kp in module["knowledge_points"]
                            )
                            for state in (
                                "insufficient_evidence",
                                "needs_support",
                                "developing",
                                "supported",
                            )
                        },
                        "updated_at": progress.updated_at,
                    }
                )
            except Exception:
                logger.warning("Failed to load progress for book %s, skipping", bid, exc_info=True)
                errors.append({"book_id": bid, "error": "Failed to load"})
                continue
        return {"summaries": summaries, "errors": errors}

    def save(self, progress: LearningProgress) -> None:
        self._store.save(progress)


def project_canonical_event_to_existing_progress(
    event: LearnerEvent,
    *,
    service: LearningService | None = None,
) -> bool:
    """Run the shared learning projection without creating a new path.

    Routers use this as their ``CanonicalAnswerEventChain`` derived callback.
    Suppressing startup replay on the short-lived adapter avoids recursively
    replaying the same ledger while its current derived operation is claimed.
    """
    projector = service or LearningService(resume_canonical_derivations=False)
    return projector.project_existing_canonical_event(event)


__all__ = ["LearningService", "project_canonical_event_to_existing_progress"]
