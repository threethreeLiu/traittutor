"""Owner-bound read projection over existing canonical learning stores."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json

from traittutor.learning.models import ErrorRecord, LearningProgress
from traittutor.learning.storage import LearningStore
from traittutor.learning_governance.models import (
    ErrorSummary,
    GovernanceAttributionStatus,
    MisconceptionSummary,
    RepairSummary,
    ReviewSummary,
    SubjectKnowledgeEvidence,
    SubjectLearningStateSnapshot,
)
from traittutor.learning_model.bkt import rebuild_knowledge_states
from traittutor.learning_model.events import LearnerEvent, LearnerEventLedger, is_strong_evidence
from traittutor.learning_model.knowledge_state import display_mastery
from traittutor.learning_model.misconception import MisconceptionStore
from traittutor.learning_model.parameters import BKTParamSet, get_active_bkt_params


@dataclass(frozen=True, slots=True)
class OwnerBoundLearningStore:
    """Explicit authentication boundary around one user's LearningStore.

    ``LearningStore`` predates multi-user DTOs and carries no owner field. The
    API composition root must construct it from the authenticated workspace and
    bind that owner here; repositories refuse a differently bound source.
    """

    owner_id: str
    store: LearningStore


class LearningGovernanceRepository:
    """Build learner-safe views without creating a second learning truth."""

    def __init__(
        self,
        *,
        owner_id: str,
        learning_source: OwnerBoundLearningStore,
        event_ledger: LearnerEventLedger,
        misconception_store: MisconceptionStore,
    ) -> None:
        normalized_owner = owner_id.strip()
        if not normalized_owner:
            raise ValueError("owner_id must not be blank")
        if learning_source.owner_id != normalized_owner:
            raise PermissionError("learning store owner mismatch")
        if misconception_store.owner_id != normalized_owner:
            raise PermissionError("misconception store must be owner-bound")
        self._owner_id = normalized_owner
        self._learning_store = learning_source.store
        self._event_ledger = event_ledger
        self._misconception_store = misconception_store

    def subject_sources(self) -> dict[str, tuple[str, ...]]:
        """Return owner-bound subject IDs represented by governance facts."""
        sources: dict[str, set[str]] = {}
        for progress in self._progresses():
            subject_id = progress.subject_id.strip()
            if not subject_id:
                continue
            if progress.error_records:
                sources.setdefault(subject_id, set()).add("error-records")
        for subject_id in self._misconception_store.list_subject_ids(user_id=self._owner_id):
            sources.setdefault(subject_id, set()).add("misconceptions")
        return {
            subject_id: tuple(sorted(subject_refs))
            for subject_id, subject_refs in sorted(sources.items())
        }

    def list_errors(self, *, subject_id: str, kc_id: str | None = None) -> list[ErrorSummary]:
        results: list[ErrorSummary] = []
        for progress in self._progresses():
            for record in progress.error_records:
                if kc_id is not None and record.knowledge_point_id != kc_id:
                    continue
                attribution = self._error_attribution(progress, record, subject_id)
                if attribution is None:
                    continue
                results.append(
                    ErrorSummary(
                        error_id=record.id,
                        question_id=record.question_id,
                        subject_id=subject_id,
                        kc_id=record.knowledge_point_id,
                        module_id=record.module_id,
                        error_type=record.error_type,
                        status=record.status,
                        attribution_status=attribution,
                        source_event_ids=tuple(record.source_event_ids),
                        created_at=record.created_at,
                        repaired_at=record.repaired_at,
                        relapsed_at=record.relapsed_at,
                        last_seen_at=record.last_seen_at,
                    )
                )
        return sorted(results, key=lambda item: (item.created_at, item.error_id))

    def list_repairs(self, *, subject_id: str, kc_id: str | None = None) -> list[RepairSummary]:
        errors_by_id = {
            item.error_id: item for item in self.list_errors(subject_id=subject_id, kc_id=kc_id)
        }
        repairs: list[RepairSummary] = []
        for progress in self._progresses():
            for record in progress.error_records:
                projected = errors_by_id.get(record.id)
                if projected is None or not (
                    record.retry_history or record.repaired_at is not None
                ):
                    continue
                repairs.append(
                    RepairSummary(
                        error_id=record.id,
                        subject_id=subject_id,
                        kc_id=record.knowledge_point_id,
                        status=record.status,
                        attribution_status=projected.attribution_status,
                        # Legacy records predate the lifetime counter; retain
                        # their visible detail count while newer compacted
                        # records use the monotonic total.
                        attempt_count=max(
                            record.total_retry_count,
                            len(record.retry_history),
                        ),
                        successful_attempt_count=sum(
                            attempt.is_correct for attempt in record.retry_history
                        ),
                        last_attempt_at=(
                            record.retry_history[-1].timestamp if record.retry_history else None
                        ),
                    )
                )
        return sorted(repairs, key=lambda item: item.error_id)

    def list_misconceptions(
        self, *, subject_id: str, kc_id: str | None = None
    ) -> list[MisconceptionSummary]:
        items = self._misconception_store.list_for(
            user_id=self._owner_id,
            subject_id=subject_id,
            kc_id=kc_id,
        )
        results: list[MisconceptionSummary] = []
        for item in items:
            evidence = self._events_for_refs(
                item.evidence_refs,
                subject_id=subject_id,
                kc_ids=set(item.kc_ids),
            )
            attribution = (
                GovernanceAttributionStatus.VERIFIED
                if any(is_strong_evidence(event) for event in evidence)
                else GovernanceAttributionStatus.ATTRIBUTION_PENDING
            )
            results.append(
                MisconceptionSummary(
                    hypothesis_id=item.hypothesis_id,
                    subject_id=item.subject_id,
                    kc_ids=item.kc_ids,
                    pattern=item.pattern,
                    status=item.status,
                    attribution_status=attribution,
                    evidence_count=len(item.evidence_refs),
                    created_at=item.created_at,
                    updated_at=item.updated_at,
                )
            )
        return results

    def list_reviews(
        self,
        *,
        subject_id: str,
        kc_id: str | None = None,
        now: float | None = None,
    ) -> list[ReviewSummary]:
        """Return no review rows until the canonical event reducer owns them.

        ``LearningProgress.review_queue`` is intentionally excluded because
        its ``ReviewTask`` rows have no source-event identity or mapping
        version. Exposing them would manufacture verified provenance.
        """
        del subject_id, kc_id, now
        return []

    def subject_learning_state_snapshot(
        self,
        *,
        subject_id: str,
        params: BKTParamSet | None = None,
    ) -> SubjectLearningStateSnapshot:
        """Rebuild a single identity-bound read model from canonical facts.

        This method is deliberately read-only. It does not persist a derived
        row or schedule reviews; only canonical events contribute.
        """
        return build_subject_learning_state_snapshot(
            owner_id=self._owner_id,
            subject_id=subject_id,
            event_ledger=self._event_ledger,
            params=params,
        )

    def _progresses(self) -> list[LearningProgress]:
        progresses: list[LearningProgress] = []
        for book_id in self._learning_store.list_all():
            progress = self._learning_store.load(book_id)
            if progress is not None:
                progresses.append(progress)
        return progresses

    def _error_attribution(
        self,
        progress: LearningProgress,
        record: ErrorRecord,
        subject_id: str,
    ) -> GovernanceAttributionStatus | None:
        events = self._events_for_refs(
            tuple(record.source_event_ids),
            subject_id=subject_id,
            kc_ids={record.knowledge_point_id},
        )
        if not events:
            return None
        if any(is_strong_evidence(event) for event in events):
            return GovernanceAttributionStatus.VERIFIED
        return GovernanceAttributionStatus.ATTRIBUTION_PENDING

    def _events_for_refs(
        self,
        refs: tuple[str, ...],
        *,
        subject_id: str,
        kc_ids: set[str],
    ) -> list[LearnerEvent]:
        events: list[LearnerEvent] = []
        for event_id in refs:
            event = self._event_ledger.get(event_id)
            if (
                event is not None
                and self._event_ledger.is_effective(event_id)
                and event.user_id == self._owner_id
                and event.subject_id == subject_id
                and kc_ids.intersection(event.kc_ids)
            ):
                events.append(event)
        return events


def build_subject_learning_state_snapshot(
    *,
    owner_id: str,
    subject_id: str,
    event_ledger: LearnerEventLedger,
    params: BKTParamSet | None = None,
) -> SubjectLearningStateSnapshot:
    """Return a deterministic owner/subject view of canonical strong evidence.

    It accepts only the canonical ledger and shared parameter set.
    """
    active_params = params or get_active_bkt_params()
    normalized_owner = owner_id.strip()
    normalized_subject = subject_id.strip()
    if not normalized_owner:
        raise ValueError("owner_id must not be blank")
    if not normalized_subject:
        raise ValueError("subject_id must not be blank")

    # ``strong_evidence_for`` applies the one canonical evidence gate and the
    # identity/subject filter before any BKT projection. It excludes weak,
    # pending and ungraded events by construction.
    events = event_ledger.strong_evidence_for(
        user_id=normalized_owner,
        subject_id=normalized_subject,
    )
    states = rebuild_knowledge_states(events, params=active_params)
    knowledge: list[SubjectKnowledgeEvidence] = []
    for unit in sorted(
        states.all_for(user_id=normalized_owner, subject_id=normalized_subject),
        key=lambda item: item.kc_id,
    ):
        public = display_mastery(unit)
        knowledge.append(
            SubjectKnowledgeEvidence(
                kc_id=unit.kc_id,
                evidence_state=public["evidence_state"],
                change_signal=public["change_signal"],
                verified_observation_count=unit.verified_observation_count,
                model_version=public["model_version"],
                stage_policy_version=public["stage_policy_version"],
            )
        )

    # Hash only the immutable facts actually permitted to contribute to the
    # projection.  ``answer_correct`` is confined to this internal digest; it
    # is never exposed by either learner-safe DTO.
    revision_input = {
        "owner_id": normalized_owner,
        "subject_id": normalized_subject,
        "params": active_params.model_dump(mode="json"),
        "events": [
            {
                "event_id": event.event_id,
                "created_at": event.created_at,
                "kc_ids": event.kc_ids,
                "answer_correct": event.answer_correct,
            }
            for event in sorted(events, key=lambda item: (item.created_at, item.event_id))
        ],
        # Include the bounded immutable audit so a void of weak evidence is
        # still visible to deterministic state/version readers, without
        # exposing a reason or any answer material in the public DTO.
        "amendments": [
            {
                "amendment_id": amendment.amendment_id,
                "target_event_id": amendment.target_event_id,
                "created_at": amendment.created_at,
            }
            for amendment in event_ledger.amendments_for(
                user_id=normalized_owner,
                subject_id=normalized_subject,
            )
        ],
    }
    encoded = json.dumps(
        revision_input,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return SubjectLearningStateSnapshot(
        owner_id=normalized_owner,
        subject_id=normalized_subject,
        source_revision=hashlib.sha256(encoded).hexdigest(),
        param_version=active_params.version,
        calibrated=active_params.calibrated,
        strong_event_count=len(events),
        knowledge=tuple(knowledge[:24]),
    )


__all__ = [
    "LearningGovernanceRepository",
    "OwnerBoundLearningStore",
    "build_subject_learning_state_snapshot",
]
