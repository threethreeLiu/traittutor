"""Read-only aggregation over the existing canonical learning services."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
import logging
from typing import Protocol, TypeVar

from traittutor import learning_packs
from traittutor.learning.models import ErrorRecordStatus
from traittutor.learning.storage import LearningStore
from traittutor.learning_governance.models import (
    GovernanceAttributionStatus,
    LearnerSubjectLearningState,
    LearningGovernanceSnapshot,
    ReviewStatus,
)
from traittutor.learning_governance.repository import (
    LearningGovernanceRepository,
    OwnerBoundLearningStore,
)
from traittutor.learning_governance.service import LearningGovernanceService
from traittutor.learning_model.events import LearnerEvent, LearnerEventLedger, is_strong_evidence
from traittutor.learning_model.misconception import MisconceptionStore
from traittutor.learning_model.read_models import (
    KcMasteryDisplay,
    KnowledgeTabSummary,
    LearningModelOverview,
    LearningModelSubjectDetail,
    LearningTask,
    PendingSubjectCard,
    PendingSubjectsSection,
    SectionMeta,
    SectionStatus,
    SubjectCard,
    SubjectHeader,
    SubjectsSection,
    SubjectTabs,
    SubjectTabSummary,
    SupportSummary,
    TaskQueueSection,
    TodaySummary,
)
from traittutor.learning_model.stage_policy import qualitative_change_signal
from traittutor.multi_user.models import CurrentUser
from traittutor.multi_user.paths import get_path_service_for_scope
from traittutor.personalization.graph_repository import LearningKnowledgeGraphRepository
from traittutor.personalization.knowledge_graph import load_learning_knowledge_graph
from traittutor.personalization.models import LearningKnowledgeGraph
from traittutor.personalization.service import PersonalizationService


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _iso_from_timestamp(value: float | None) -> str | None:
    return datetime.fromtimestamp(value, tz=UTC).isoformat() if value else None


_T = TypeVar("_T")
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SubjectSeed:
    subject_id: str
    label: str
    confirmed: bool = True
    updated_at: str | None = None
    source_refs: tuple[str, ...] = ()
    covered_kc_count: int = 0
    strong_evidence_count: int = 0
    attribution_pending_count: int = 0
    needs_rebuild: bool = False


@dataclass(frozen=True, slots=True)
class SupportFacts:
    inference_enabled: bool
    confirmed_preference_count: int = 0
    confirmed_reflection_count: int = 0
    compass_signal_count: int = 0
    updated_at: str | None = None
    source_refs: tuple[str, ...] = ()


class LearningModelSources(Protocol):
    owner_id: str

    def profile_subjects(self) -> Sequence[SubjectSeed]: ...

    def progress_subjects(self) -> Sequence[SubjectSeed]: ...

    def pack_subjects(self) -> Sequence[SubjectSeed]: ...

    def event_subjects(self) -> Sequence[SubjectSeed]: ...

    def governance_subjects(self) -> Sequence[SubjectSeed]: ...

    def knowledge_graph_subjects(self) -> Sequence[SubjectSeed]: ...

    def governance(self, subject_id: str) -> LearningGovernanceSnapshot: ...

    def learning_state(self, subject_id: str) -> LearnerSubjectLearningState: ...

    def knowledge_graph(self, subject_id: str) -> LearningKnowledgeGraph | None: ...

    def support(self, subject_id: str | None = None) -> SupportFacts: ...


class LearningModelSubjectNotFound(LookupError):
    """Generic missing/unauthorized signal; API must not disclose which."""


@dataclass(slots=True)
class _MergedSubject:
    subject_id: str
    label: str
    confirmed: bool = True
    updated_at: str | None = None
    source_refs: set[str] = field(default_factory=set)
    covered_kc_count: int = 0
    strong_evidence_count: int = 0
    attribution_pending_count: int = 0
    needs_rebuild: bool = False

    def merge(self, seed: SubjectSeed) -> None:
        if seed.label and (self.label == self.subject_id or seed.confirmed):
            self.label = seed.label
        self.confirmed = self.confirmed and seed.confirmed
        self.updated_at = max(filter(None, (self.updated_at, seed.updated_at)), default=None)
        self.source_refs.update(seed.source_refs)
        self.covered_kc_count = max(self.covered_kc_count, seed.covered_kc_count)
        self.strong_evidence_count = max(self.strong_evidence_count, seed.strong_evidence_count)
        self.attribution_pending_count += seed.attribution_pending_count
        self.needs_rebuild = self.needs_rebuild or seed.needs_rebuild


@dataclass(frozen=True, slots=True)
class _SubjectFacts:
    governance: LearningGovernanceSnapshot | None
    state: LearnerSubjectLearningState | None
    graph: LearningKnowledgeGraph | None
    failures: tuple[str, ...]


class LearningModelReadService:
    """Build page projections without writing or creating a new truth store."""

    def __init__(self, *, owner_id: str, sources: LearningModelSources) -> None:
        normalized_owner = owner_id.strip()
        if not normalized_owner or sources.owner_id != normalized_owner:
            raise PermissionError("learning-model source owner mismatch")
        self._owner_id = normalized_owner
        self._sources = sources

    def overview(self) -> LearningModelOverview:
        generated_at = _now()
        subjects, discovery_failures = self._discover_subjects()
        confirmed = [item for item in subjects.values() if item.confirmed]
        pending = [item for item in subjects.values() if not item.confirmed]

        cards: list[SubjectCard] = []
        tasks: list[LearningTask] = []
        fact_failures: set[str] = set()
        for subject in sorted(confirmed, key=lambda item: item.subject_id):
            facts = self._subject_facts(subject.subject_id)
            fact_failures.update(facts.failures)
            cards.append(self._subject_card(subject, facts))
            tasks.extend(self._tasks(subject, facts.governance))

        all_failures = tuple(sorted(set(discovery_failures) | fact_failures))
        source_refs = tuple(sorted({ref for item in cards for ref in item.source_refs}))
        latest_activity = max(
            (item.last_activity_at for item in cards if item.last_activity_at), default=None
        )
        pending_items = tuple(
            PendingSubjectCard(
                subject_id=item.subject_id,
                label=item.label,
                created_at=item.updated_at,
                source_refs=tuple(sorted(item.source_refs)),
                possible_duplicate_subject_ids=tuple(
                    sorted(
                        other.subject_id
                        for other in confirmed
                        if other.label.casefold() == item.label.casefold()
                        and other.subject_id != item.subject_id
                    )
                ),
            )
            for item in sorted(pending, key=lambda value: value.subject_id)
        )
        support = self._support_section()
        due_reviews = sum(item.due_review_count for item in cards)
        open_errors = sum(item.open_error_count for item in cards)
        attribution_pending = sum(item.attribution_pending_count for item in confirmed)
        return LearningModelOverview(
            generated_at=generated_at,
            today=TodaySummary(
                meta=self._meta(
                    item_count=len(cards) + due_reviews + open_errors + attribution_pending,
                    source_refs=source_refs,
                    failures=all_failures,
                    updated_at=latest_activity,
                ),
                active_subject_count=len(cards),
                due_review_count=due_reviews,
                open_error_count=open_errors,
                attribution_pending_count=attribution_pending,
                latest_activity_at=latest_activity,
            ),
            confirmed_subjects=SubjectsSection(
                meta=self._meta(
                    item_count=len(cards),
                    source_refs=source_refs,
                    failures=all_failures,
                    updated_at=latest_activity,
                ),
                items=tuple(cards),
            ),
            pending_subjects=PendingSubjectsSection(
                meta=self._meta(
                    item_count=len(pending_items),
                    source_refs=("learner-profile",),
                    failures=("learner-profile",)
                    if "learner-profile" in discovery_failures
                    else (),
                    updated_at=max(
                        (item.created_at for item in pending_items if item.created_at),
                        default=None,
                    ),
                ),
                items=pending_items,
            ),
            task_queue=TaskQueueSection(
                meta=self._meta(
                    item_count=len(tasks),
                    source_refs=("learning-governance", "learner-event-ledger"),
                    failures=all_failures,
                ),
                items=tuple(sorted(tasks, key=lambda item: (item.due_at or "", item.task_id))),
            ),
            support=support,
        )

    def subject_detail(self, subject_id: str) -> LearningModelSubjectDetail:
        normalized = subject_id.strip()
        if not normalized or len(normalized) > 96:
            raise LearningModelSubjectNotFound
        subjects, discovery_failures = self._discover_subjects()
        subject = subjects.get(normalized)
        if subject is None:
            raise LearningModelSubjectNotFound
        facts = self._subject_facts(normalized)
        failures = tuple(sorted(set(discovery_failures) | set(facts.failures)))
        governance = facts.governance
        state = facts.state
        graph = facts.graph
        errors = governance.errors if governance else ()
        reviews = governance.reviews if governance else ()
        misconceptions = governance.misconceptions if governance else ()
        knowledge_ids = {
            *(item.kc_id for item in (state.knowledge if state else ())),
            *(node.concept_id for node in (graph.nodes if graph else ())),
        }
        mastery_items = tuple(
            KcMasteryDisplay(
                kc_id=item.kc_id,
                # Error lifecycle and BKT evidence sufficiency are separate
                # facts. An open error can request support through the change
                # signal, but cannot turn an uncalibrated BKT cell into a
                # different evidence state.
                evidence_state=item.evidence_state,
                change_signal=qualitative_change_signal(
                    has_open_or_relapsed_error=any(
                        error.kc_id == item.kc_id
                        and error.status in {ErrorRecordStatus.OPEN, ErrorRecordStatus.RELAPSED}
                        for error in errors
                    ),
                    repaired=any(
                        error.kc_id == item.kc_id and error.status == ErrorRecordStatus.REPAIRED
                        for error in errors
                    ),
                    has_due_review=any(
                        review.kc_id == item.kc_id and review.status == ReviewStatus.DUE
                        for review in reviews
                    ),
                ),
                verified_observation_count=item.verified_observation_count,
                model_version=item.model_version,
                stage_policy_version=item.stage_policy_version,
            )
            for item in (state.knowledge if state else ())
        )
        updated_at = max(
            filter(
                None,
                (
                    subject.updated_at,
                    graph.updated_at if graph else None,
                ),
            ),
            default=None,
        )
        rebuilding = subject.needs_rebuild or any(
            review.status == ReviewStatus.NEEDS_REBUILD for review in reviews
        )
        common_refs = tuple(sorted(subject.source_refs))
        governance_meta = self._meta(
            item_count=len(errors) + len(reviews) + len(misconceptions),
            source_refs=(*common_refs, "learning-governance"),
            failures=failures,
            updated_at=updated_at,
            rebuilding=rebuilding,
        )
        support = self._safe_call("learner-support", lambda: self._sources.support(normalized))
        support_facts, support_error = support
        support_failures = ("learner-support",) if support_error else ()
        return LearningModelSubjectDetail(
            generated_at=_now(),
            header=SubjectHeader(
                subject_id=subject.subject_id,
                label=subject.label,
                confirmed=subject.confirmed,
                updated_at=updated_at,
                data_status=governance_meta.status,
            ),
            tabs=SubjectTabs(
                overview=SubjectTabSummary(
                    meta=governance_meta,
                    item_count=len(knowledge_ids) + len(errors) + len(reviews),
                    actionable_count=sum(
                        error.status in {ErrorRecordStatus.OPEN, ErrorRecordStatus.RELAPSED}
                        for error in errors
                    )
                    + sum(review.status == ReviewStatus.DUE for review in reviews),
                ),
                knowledge=KnowledgeTabSummary(
                    meta=self._meta(
                        item_count=len(knowledge_ids),
                        source_refs=(*common_refs, "canonical-bkt", "knowledge-graph"),
                        failures=tuple(
                            item
                            for item in failures
                            if item in {"canonical-bkt", "knowledge-graph"}
                        ),
                        updated_at=updated_at,
                        rebuilding=rebuilding,
                    ),
                    item_count=len(knowledge_ids),
                    actionable_count=sum(
                        item.evidence_state in {"insufficient_evidence", "needs_support"}
                        for item in mastery_items
                    ),
                    mastery_items=mastery_items,
                    model_version=state.param_version if state else None,
                    mapping_version=str(graph.version) if graph else None,
                ),
                errors=SubjectTabSummary(
                    meta=governance_meta,
                    item_count=len(errors),
                    actionable_count=sum(
                        item.status in {ErrorRecordStatus.OPEN, ErrorRecordStatus.RELAPSED}
                        for item in errors
                    ),
                ),
                reviews=SubjectTabSummary(
                    meta=governance_meta,
                    item_count=len(reviews),
                    actionable_count=sum(item.status == ReviewStatus.DUE for item in reviews),
                ),
                misconceptions=SubjectTabSummary(
                    meta=governance_meta,
                    item_count=len(misconceptions),
                    actionable_count=sum(
                        item.attribution_status == GovernanceAttributionStatus.ATTRIBUTION_PENDING
                        for item in misconceptions
                    ),
                ),
                support=SubjectTabSummary(
                    meta=self._meta(
                        item_count=(
                            support_facts.confirmed_preference_count
                            + support_facts.confirmed_reflection_count
                            + support_facts.compass_signal_count
                            if support_facts
                            else 0
                        ),
                        source_refs=support_facts.source_refs if support_facts else (),
                        failures=support_failures,
                        updated_at=support_facts.updated_at if support_facts else None,
                    ),
                    item_count=(
                        support_facts.confirmed_preference_count
                        + support_facts.confirmed_reflection_count
                        + support_facts.compass_signal_count
                        if support_facts
                        else 0
                    ),
                ),
                governance=SubjectTabSummary(
                    meta=governance_meta,
                    item_count=(state.strong_event_count if state else 0)
                    + subject.attribution_pending_count,
                    actionable_count=subject.attribution_pending_count + int(rebuilding),
                ),
            ),
            allowed_actions=(
                "continue_learning",
                "view_evidence",
                *(
                    ("confirm_subject", "correct_subject")
                    if not subject.confirmed
                    else ("correct_subject",)
                ),
                *(
                    ("start_review",)
                    if any(item.status == ReviewStatus.DUE for item in reviews)
                    else ()
                ),
                *(
                    ("repair_error",)
                    if any(
                        item.status in {ErrorRecordStatus.OPEN, ErrorRecordStatus.RELAPSED}
                        for item in errors
                    )
                    else ()
                ),
            ),
        )

    def _discover_subjects(self) -> tuple[dict[str, _MergedSubject], tuple[str, ...]]:
        merged: dict[str, _MergedSubject] = {}
        failures: list[str] = []
        for source_name, loader in (
            ("learner-profile", self._sources.profile_subjects),
            ("learning-progress", self._sources.progress_subjects),
            ("learning-packs", self._sources.pack_subjects),
            ("learner-event-ledger", self._sources.event_subjects),
            ("learning-governance", self._sources.governance_subjects),
            ("knowledge-graph", self._sources.knowledge_graph_subjects),
        ):
            values, failed = self._safe_call(source_name, loader)
            if failed:
                failures.append(source_name)
                continue
            for seed in values or ():
                normalized = seed.subject_id.strip()
                if not normalized or len(normalized) > 96:
                    continue
                existing = merged.setdefault(
                    normalized,
                    _MergedSubject(subject_id=normalized, label=seed.label or normalized),
                )
                existing.merge(seed)
        return merged, tuple(failures)

    def _subject_facts(self, subject_id: str) -> _SubjectFacts:
        governance, governance_failed = self._safe_call(
            "learning-governance", lambda: self._sources.governance(subject_id)
        )
        state, state_failed = self._safe_call(
            "canonical-bkt", lambda: self._sources.learning_state(subject_id)
        )
        graph, graph_failed = self._safe_call(
            "knowledge-graph", lambda: self._sources.knowledge_graph(subject_id)
        )
        return _SubjectFacts(
            governance=governance,
            state=state,
            graph=graph,
            failures=tuple(
                name
                for name, failed in (
                    ("learning-governance", governance_failed),
                    ("canonical-bkt", state_failed),
                    ("knowledge-graph", graph_failed),
                )
                if failed
            ),
        )

    def _subject_card(self, subject: _MergedSubject, facts: _SubjectFacts) -> SubjectCard:
        governance = facts.governance
        state = facts.state
        graph = facts.graph
        errors = governance.errors if governance else ()
        reviews = governance.reviews if governance else ()
        refs = set(subject.source_refs)
        if governance is not None:
            refs.add("learning-governance")
        if state is not None:
            refs.add("canonical-bkt")
        if graph is not None:
            refs.add("knowledge-graph")
        return SubjectCard(
            subject_id=subject.subject_id,
            label=subject.label,
            data_status=self._meta(
                item_count=1,
                source_refs=tuple(refs),
                failures=facts.failures,
                rebuilding=subject.needs_rebuild,
            ).status,
            last_activity_at=subject.updated_at,
            covered_kc_count=max(
                subject.covered_kc_count,
                len(state.knowledge) if state else 0,
                len(graph.nodes) if graph else 0,
            ),
            strong_evidence_count=max(
                subject.strong_evidence_count,
                state.strong_event_count if state else 0,
            ),
            open_error_count=sum(
                item.status in {ErrorRecordStatus.OPEN, ErrorRecordStatus.RELAPSED}
                for item in errors
            ),
            due_review_count=sum(item.status == ReviewStatus.DUE for item in reviews),
            source_refs=tuple(sorted(refs)),
        )

    @staticmethod
    def _tasks(
        subject: _MergedSubject,
        governance: LearningGovernanceSnapshot | None,
    ) -> list[LearningTask]:
        tasks: list[LearningTask] = []
        if governance is not None:
            for review in governance.reviews:
                if review.status == ReviewStatus.DUE:
                    tasks.append(
                        LearningTask(
                            task_id=f"review:{review.review_id}",
                            subject_id=subject.subject_id,
                            kind="review",
                            due_at=_iso_from_timestamp(review.due_at),
                            source_refs=(review.review_id,),
                        )
                    )
            for error in governance.errors:
                if error.status in {ErrorRecordStatus.OPEN, ErrorRecordStatus.RELAPSED}:
                    tasks.append(
                        LearningTask(
                            task_id=f"error:{error.error_id}",
                            subject_id=subject.subject_id,
                            kind="error_repair",
                            source_refs=(error.error_id,),
                        )
                    )
        if subject.attribution_pending_count:
            tasks.append(
                LearningTask(
                    task_id=f"attribution:{subject.subject_id}",
                    subject_id=subject.subject_id,
                    kind="attribution",
                    source_refs=("learner-event-ledger",),
                )
            )
        return tasks

    def _support_section(self) -> SupportSummary:
        facts, failed = self._safe_call("learner-support", self._sources.support)
        if facts is None:
            return SupportSummary(
                meta=self._meta(item_count=0, failures=("learner-support",)),
            )
        item_count = (
            facts.confirmed_preference_count
            + facts.confirmed_reflection_count
            + facts.compass_signal_count
        )
        return SupportSummary(
            meta=self._meta(
                item_count=item_count,
                source_refs=facts.source_refs,
                failures=("learner-support",) if failed else (),
                updated_at=facts.updated_at,
            ),
            inference_enabled=facts.inference_enabled,
            confirmed_preference_count=facts.confirmed_preference_count,
            confirmed_reflection_count=facts.confirmed_reflection_count,
            compass_signal_count=facts.compass_signal_count,
        )

    @staticmethod
    def _safe_call(name: str, loader: Callable[[], _T]) -> tuple[_T | None, bool]:
        try:
            return loader(), False
        except Exception:
            logger.warning("learning-model source unavailable: %s", name, exc_info=True)
            return None, True

    @staticmethod
    def _meta(
        *,
        item_count: int,
        source_refs: Sequence[str] = (),
        failures: Sequence[str] = (),
        updated_at: str | None = None,
        rebuilding: bool = False,
    ) -> SectionMeta:
        unique_failures = tuple(sorted(set(failures)))
        if rebuilding:
            status = SectionStatus.REBUILDING
        elif unique_failures and item_count == 0 and set(source_refs).issubset(unique_failures):
            status = SectionStatus.UNAVAILABLE
        elif unique_failures:
            status = SectionStatus.STALE
        elif item_count:
            status = SectionStatus.READY
        else:
            status = SectionStatus.EMPTY
        return SectionMeta(
            status=status,
            updated_at=updated_at,
            source_refs=tuple(dict.fromkeys(source_refs))[:24],
            unavailable_sources=unique_failures,
        )


class CanonicalLearningModelSources:
    """Production adapter over existing owner-scoped stores and read services."""

    def __init__(self, user: CurrentUser) -> None:
        self.owner_id = user.id
        path_service = get_path_service_for_scope(user.scope)
        workspace = path_service.get_workspace_dir()
        self._learning_store = LearningStore(
            workspace / "learning",
            path_service=path_service,
            owner_id=user.id,
        )
        self._event_ledger = LearnerEventLedger(
            workspace / "learning_model" / "learner_events.json",
            path_service=get_path_service_for_scope(user.scope),
        )
        self._misconception_store = MisconceptionStore(
            workspace / "learning_model" / "misconceptions.json",
            owner_id=user.id,
            path_service=get_path_service_for_scope(user.scope),
        )
        self._knowledge_graph_repository = LearningKnowledgeGraphRepository(
            path_service.get_traittutor_database_path()
        )
        self._personalization = PersonalizationService()
        self._governance_repository = LearningGovernanceRepository(
            owner_id=user.id,
            learning_source=OwnerBoundLearningStore(
                owner_id=user.id,
                store=self._learning_store,
            ),
            event_ledger=self._event_ledger,
            misconception_store=self._misconception_store,
        )
        self._governance = LearningGovernanceService(self._governance_repository)

    def profile_subjects(self) -> Sequence[SubjectSeed]:
        return tuple(
            SubjectSeed(
                subject_id=profile.subject.subject_id,
                label=profile.subject.label,
                confirmed=profile.subject.confirmed,
                updated_at=profile.updated_at,
                source_refs=("learner-profile", *profile.evidence_refs),
                covered_kc_count=len(profile.concept_signals),
                needs_rebuild=profile.needs_rebuild,
            )
            for profile in self._personalization.subjects()
            if profile.subject is not None
        )

    def progress_subjects(self) -> Sequence[SubjectSeed]:
        seeds: list[SubjectSeed] = []
        for book_id in self._learning_store.list_all():
            progress = self._learning_store.load(book_id)
            if progress is None or not progress.subject_id.strip():
                continue
            seeds.append(
                SubjectSeed(
                    subject_id=progress.subject_id,
                    label=progress.subject_id,
                    updated_at=_iso_from_timestamp(progress.updated_at),
                    source_refs=(f"learning-path:{progress.book_id}",),
                    covered_kc_count=len(
                        {
                            point.id
                            for module in progress.modules
                            for point in module.knowledge_points
                        }
                    ),
                )
            )
        return tuple(seeds)

    def event_subjects(self) -> Sequence[SubjectSeed]:
        by_subject: dict[str, list[LearnerEvent]] = {}
        for event in self._event_ledger.effective_events():
            if event.user_id == self.owner_id and event.subject_id:
                by_subject.setdefault(event.subject_id, []).append(event)
        return tuple(
            SubjectSeed(
                subject_id=subject_id,
                label=subject_id,
                updated_at=max(event.created_at for event in events),
                source_refs=("learner-event-ledger",),
                covered_kc_count=len({kc for event in events for kc in event.kc_ids}),
                strong_evidence_count=sum(is_strong_evidence(event) for event in events),
                attribution_pending_count=sum(
                    event.attribution_status == "attribution_pending" for event in events
                ),
            )
            for subject_id, events in by_subject.items()
        )

    def pack_subjects(self) -> Sequence[SubjectSeed]:
        seeds: list[SubjectSeed] = []
        for pack in learning_packs.list_packs():
            binding = learning_packs.active_learning_path_binding(
                pack,
                owner_id=self.owner_id,
            )
            if binding is None:
                continue
            subject_id = str(binding.get("subject_id") or "").strip()
            if not subject_id:
                continue
            allowed_kcs = binding.get("allowed_kc_ids")
            seeds.append(
                SubjectSeed(
                    subject_id=subject_id,
                    label=subject_id,
                    updated_at=str(pack.get("updated_at") or "") or None,
                    source_refs=(f"learning-pack:{pack.get('pack_id')}",),
                    covered_kc_count=len(allowed_kcs) if isinstance(allowed_kcs, list) else 0,
                )
            )
        return tuple(seeds)

    def governance_subjects(self) -> Sequence[SubjectSeed]:
        return tuple(
            SubjectSeed(
                subject_id=subject_id,
                label=subject_id,
                source_refs=source_refs,
            )
            for subject_id, source_refs in self._governance_repository.subject_sources().items()
        )

    def knowledge_graph_subjects(self) -> Sequence[SubjectSeed]:
        seeds: list[SubjectSeed] = []
        for subject_id in self._knowledge_graph_repository.list_subject_ids():
            graph = self._knowledge_graph_repository.load(subject_id)
            if graph is None:
                continue
            seeds.append(
                SubjectSeed(
                    subject_id=subject_id,
                    label=graph.subject.label,
                    confirmed=graph.subject.confirmed,
                    updated_at=graph.updated_at,
                    source_refs=("knowledge-graph", *graph.source_refs),
                    covered_kc_count=len(graph.nodes),
                )
            )
        return tuple(seeds)

    def governance(self, subject_id: str) -> LearningGovernanceSnapshot:
        return self._governance.snapshot(subject_id=subject_id)

    def learning_state(self, subject_id: str) -> LearnerSubjectLearningState:
        snapshot = self._governance.subject_learning_state_snapshot(subject_id=subject_id)
        return LearnerSubjectLearningState.model_validate(snapshot.model_dump(exclude={"owner_id"}))

    def knowledge_graph(self, subject_id: str) -> LearningKnowledgeGraph | None:
        return load_learning_knowledge_graph(subject_id)

    def support(self, subject_id: str | None = None) -> SupportFacts:
        profiles = (
            [self._personalization.subject_profile(subject_id)]
            if subject_id
            else [self._personalization.global_profile()]
        )
        reflections = self._personalization.reflections(subject_id=subject_id)
        return SupportFacts(
            inference_enabled=profiles[0].inference_enabled,
            confirmed_preference_count=sum(
                preference.state == "explicit"
                for profile in profiles
                for preference in profile.preferences
            ),
            confirmed_reflection_count=sum(item.status == "confirmed" for item in reflections),
            compass_signal_count=sum(item.applies_to_compass for item in reflections),
            updated_at=max((profile.updated_at for profile in profiles), default=None),
            source_refs=("learner-profile", "reflection", "compass"),
        )


__all__ = [
    "CanonicalLearningModelSources",
    "LearningModelReadService",
    "LearningModelSources",
    "LearningModelSubjectNotFound",
    "SubjectSeed",
    "SupportFacts",
]
