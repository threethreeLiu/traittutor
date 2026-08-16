"""Deterministic assembly of minimal, versioned context references."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
import hashlib
import json
import logging
import re
from typing import TYPE_CHECKING, Any, Literal

from traittutor.research_workspace.provenance import ResearchCoursewareProvenance

from .access import MemoryAccessLog, MemoryAccessRecord
from .snapshot import (
    AssistantContextSnapshot,
    AssistantIntent,
    ConceptSignalRef,
    LearningContextSnapshot,
    MemoryRef,
    SnapshotReadRanges,
    SubjectLearningStateRef,
    TutorPersonaRef,
)

if TYPE_CHECKING:
    from traittutor.conversation.retrieval import (
        ConversationRetrievalResult,
        ConversationRetrievalService,
    )
    from traittutor.learning_governance.models import SubjectLearningStateSnapshot
    from traittutor.memory.models import UserMemoryItem
    from traittutor.memory.store import MemoryStore
    from traittutor.personalization.models import PersonalizationContext
    from traittutor.tutor_persona.context_adapter import TutorPersonaContext
    from traittutor.tutor_persona.store import TutorPersonaStore


_logger = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _stable_digest(value: object) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ContextAssembler:
    """Build one immutable snapshot while keeping optional reads best-effort.

    All integrations are behind small read seams and imported lazily because
    conversation, research, and unified memory models are arriving in later
    workstreams.  A failed seam only marks the snapshot degraded; it never
    blocks the main assistant chain and never attempts an LLM fallback.
    """

    def __init__(
        self,
        access_log: MemoryAccessLog | None = None,
        *,
        canonical_memory_store_factory: Callable[[str], MemoryStore] | None = None,
        conversation_retrieval_service_factory: (
            Callable[[str], ConversationRetrievalService] | None
        ) = None,
        tutor_persona_store_factory: Callable[[str], TutorPersonaStore] | None = None,
        subject_learning_state_snapshot_factory: (
            Callable[[str, str], SubjectLearningStateSnapshot] | None
        ) = None,
    ) -> None:
        self.access_log = access_log or MemoryAccessLog()
        self._canonical_memory_store_factory = canonical_memory_store_factory
        self._conversation_retrieval_service_factory = conversation_retrieval_service_factory
        self._tutor_persona_store_factory = tutor_persona_store_factory
        self._subject_learning_state_snapshot_factory = subject_learning_state_snapshot_factory
        # Request-local teaching context derived by the same bounded read that
        # produced the immutable snapshot.
        self.personalization_context: PersonalizationContext | None = None
        # This request-local attachment is already bounded and injection
        # screened by ``ConversationRetrievalService``.  The frozen snapshot
        # still retains only episode references, never this summary text.
        self.conversation_context: ConversationRetrievalResult | None = None
        # This attachment shares the personalization context's request-local
        # lifetime. It is never a second profile store or a prompt body.
        self.tutor_persona_context: TutorPersonaContext | None = None

    def assemble(
        self,
        *,
        intent: AssistantIntent,
        user_id: str,
        token_budget: int,
        subject_id: str | None = None,
        project_id: str | None = None,
        thread_id: str | None = None,
        trace_id: str | None = None,
        created_at: str | None = None,
        prompt_bundle_id: str | None = None,
        token_used: int = 0,
        trim_reason: str | None = None,
        research_run_id: str | None = None,
        research_provenance: ResearchCoursewareProvenance | None = None,
        include_personalization: bool = True,
        include_tutor_persona: bool = True,
        user_authorized: bool = False,
        teaching_plan_ref: str | None = None,
        component_plan_ref: str | None = None,
        surface_type: str | None = None,
        page_id: str | None = None,
        memory_query: str | None = None,
        canonical_memory_limit: int = 6,
        canonical_memory_token_budget: int | None = None,
    ) -> AssistantContextSnapshot:
        """Assemble version references and degrade safely on subsystem errors.

        The existing ``PersonalizationContext`` is reduced to references to its
        bounded ``LearnerMemorySnapshot`` and canonical ``ConceptSignal``
        instances; no profile payload or BKT value is copied into this module.
        Supplying ``created_at`` and ``trace_id`` gives replay callers a fully
        reproducible hash, while normal online calls receive UTC values.

        ``user_authorized`` defaults to ``False`` (fail-closed): personalization
        reads are skipped unless a caller explicitly asserts the user authorized
        this turn (invariants #7, #12). A forgotten
        flag must never silently pull private partitions.
        """
        if (
            research_provenance is not None
            and research_run_id is not None
            and research_run_id != research_provenance.research_run_id
        ):
            raise ValueError("research_run_id must match research provenance")
        if research_provenance is not None:
            research_run_id = research_provenance.research_run_id

        assembled_at = created_at or _utc_now()
        reasons: list[str] = []
        memory_refs: list[MemoryRef] = []
        concept_refs: list[ConceptSignalRef] = []
        canonical_memory_items: list[UserMemoryItem] = []
        canonical_memory_store: MemoryStore | None = None
        tutor_persona_ref: TutorPersonaRef | None = None
        subject_learning_state_ref: SubjectLearningStateRef | None = None
        learner_profile_version: str | None = None
        derived_teaching_plan_ref: str | None = None
        active_branch_version: str | None = None
        resolved_episode_ids: list[str] = []
        self.personalization_context = None
        self.conversation_context = None
        self.tutor_persona_context = None
        conversation_thread_version: str | None = None
        conversation_retrieval_attempted = False

        if user_authorized:
            if include_personalization:
                try:
                    context, learner_profile_version = self._read_personalization_context(
                        intent=intent,
                        subject_id=subject_id,
                        user_id=user_id,
                    )
                    try:
                        (
                            canonical_memory_store,
                            canonical_memory_items,
                            canonical_retrieval_reasons,
                        ) = self._read_canonical_memory(
                            user_id=user_id,
                            subject_id=subject_id,
                            project_id=project_id,
                            purpose=f"context_assembler:{intent}",
                            memory_query=memory_query,
                            limit=canonical_memory_limit,
                            token_budget=(
                                min(512, max(0, token_budget - token_used))
                                if canonical_memory_token_budget is None
                                else canonical_memory_token_budget
                            ),
                        )
                        reasons.extend(canonical_retrieval_reasons)
                        canonical_memory_refs, context, canonical_reasons = (
                            self._apply_canonical_memory_context(
                                context,
                                canonical_memory_items,
                            )
                        )
                        memory_refs.extend(canonical_memory_refs)
                        reasons.extend(canonical_reasons)
                    except Exception:
                        _logger.exception("context_assembler canonical memory read failed")
                        reasons.append("canonical_memory_read_failed")
                    if subject_id is not None:
                        try:
                            subject_snapshot = self._read_subject_learning_state_snapshot(
                                user_id=user_id,
                                subject_id=subject_id,
                            )
                            if subject_snapshot is not None:
                                context = self._apply_subject_learning_state_context(
                                    context,
                                    subject_snapshot,
                                )
                                subject_learning_state_ref = SubjectLearningStateRef(
                                    owner_id=subject_snapshot.owner_id,
                                    subject_id=subject_snapshot.subject_id,
                                    source_revision=subject_snapshot.source_revision,
                                    param_version=subject_snapshot.param_version,
                                    strong_event_count=subject_snapshot.strong_event_count,
                                    calibrated=subject_snapshot.calibrated,
                                )
                        except Exception:
                            _logger.exception("context_assembler subject state read failed")
                            reasons.append("subject_learning_state_read_failed")
                    if include_tutor_persona:
                        try:
                            tutor_persona_context = self._read_tutor_persona_context(
                                user_id=user_id
                            )
                            if tutor_persona_context is not None:
                                context = self._apply_tutor_persona_context(
                                    context,
                                    tutor_persona_context,
                                )
                                self.tutor_persona_context = tutor_persona_context
                                tutor_persona_ref = TutorPersonaRef(
                                    profile_ref=tutor_persona_context.profile_ref,
                                    contract_hash=tutor_persona_context.contract_hash,
                                )
                        except Exception:
                            # An absent profile is an intentional no-op. A
                            # corrupt or wrong-owner record instead fails
                            # closed and leaves visible degradation evidence.
                            _logger.exception("context_assembler tutor persona read failed")
                            reasons.append("tutor_persona_read_failed")
                    self.personalization_context = context
                    personalization_memory, concept_refs, derived_teaching_plan_ref = (
                        self._personalization_refs(context)
                    )
                    if personalization_memory is not None:
                        memory_refs.append(personalization_memory)
                    if context.degraded:
                        reasons.append(
                            f"personalization_degraded:{context.degradation_reason or 'unknown'}"
                        )
                except Exception:
                    _logger.exception("context_assembler personalization read failed")
                    reasons.append("personalization_read_failed")

            if thread_id is not None:
                conversation_retrieval_attempted = True
                try:
                    conversation_context = self._read_conversation_context(
                        thread_id,
                        user_id=user_id,
                        subject_id=subject_id,
                        project_id=project_id,
                    )
                    self.conversation_context = conversation_context
                    conversation_thread_version = conversation_context.thread_version
                    active_branch_version = conversation_context.active_branch_version
                    resolved_episode_ids.extend(
                        item.episode_id for item in conversation_context.episodes
                    )
                    memory_refs.extend(
                        MemoryRef(
                            scope="conversation_episode",
                            key=item.episode_id,
                            version=f"v{item.summary_version}",
                        )
                        for item in conversation_context.episodes
                    )
                    reasons.extend(conversation_context.degradation_reasons)
                    if self.personalization_context is not None and conversation_context.episodes:
                        self.personalization_context = self._apply_conversation_context(
                            self.personalization_context,
                            conversation_context,
                        )
                except Exception:
                    _logger.exception("context_assembler conversation read failed")
                    reasons.append("conversation_read_failed")

        # Only the authorized conversation retrieval service may bind a thread
        # version into a snapshot.
        resolved_thread_version = (
            conversation_thread_version if conversation_retrieval_attempted else None
        )

        memory_refs = sorted(
            set(memory_refs),
            key=lambda item: (item.scope, item.key, item.version or ""),
        )
        concept_refs = sorted(
            set(concept_refs),
            key=lambda item: (item.concept_id, item.version),
        )
        read_ranges = SnapshotReadRanges(
            thread_version=resolved_thread_version,
            active_branch_version=active_branch_version,
            episode_ids=list(dict.fromkeys(resolved_episode_ids)),
            memory_refs=memory_refs,
            research_run_id=research_run_id,
            research_provenance=research_provenance,
            learner_profile_version=learner_profile_version,
            concept_signal_refs=concept_refs,
            tutor_persona_ref=tutor_persona_ref,
            subject_learning_state_ref=subject_learning_state_ref,
        )

        effective_used = max(0, token_used)
        effective_trim_reason = trim_reason
        if effective_used > token_budget:
            effective_used = max(0, token_budget)
            effective_trim_reason = effective_trim_reason or "token_budget_exceeded"

        resolved_trace_id = trace_id or self._derive_trace_id(
            intent=intent,
            user_id=user_id,
            subject_id=subject_id,
            thread_id=thread_id,
            prompt_bundle_id=prompt_bundle_id,
            read_ranges=read_ranges,
        )
        common: dict[str, Any] = {
            "snapshot_id": "",
            "trace_id": resolved_trace_id,
            "created_at": assembled_at,
            "intent": intent,
            "user_id": user_id,
            "subject_id": subject_id,
            "thread_id": thread_id,
            "prompt_bundle_id": prompt_bundle_id,
            "token_budget": max(0, token_budget),
            "token_used": effective_used,
            "trim_reason": effective_trim_reason,
            "read_ranges": read_ranges,
            "degraded": bool(reasons),
            "degradation_reason": ";".join(dict.fromkeys(reasons)) or None,
        }
        snapshot: AssistantContextSnapshot
        if intent == "learn":
            snapshot = LearningContextSnapshot(
                **common,
                teaching_plan_ref=teaching_plan_ref or derived_teaching_plan_ref,
                component_plan_ref=component_plan_ref,
                surface_type=surface_type,
                page_id=page_id,
            )
        else:
            snapshot = AssistantContextSnapshot(**common)

        try:
            self._record_accesses(
                snapshot=snapshot,
                memory_refs=memory_refs,
                concept_refs=concept_refs,
                learner_profile_version=learner_profile_version,
                learner_profile_key=subject_id or "global",
                tutor_persona_ref=tutor_persona_ref,
                subject_learning_state_ref=subject_learning_state_ref,
                purpose=f"context_assembler:{intent}",
                user_authorized=user_authorized,
                # Cross-domain reads (learner profile, tutor persona,
                # subject state, concept signals) get a durable audit via
                # the user's canonical memory store; canonical memory items
                # themselves are audited by ``record_accesses`` below, so
                # they are excluded here to avoid double rows.
                persist_cross_domain_to=canonical_memory_store,
            )
        except Exception:
            # A custom/durable log adapter must not take the assistant offline.
            _logger.exception("context_assembler memory access log failed")
            snapshot = snapshot.model_copy(
                update={
                    "degraded": True,
                    "degradation_reason": self._append_reason(
                        snapshot.degradation_reason,
                        "memory_access_log_failed",
                    ),
                }
            )
        if canonical_memory_store is not None and canonical_memory_items:
            try:
                canonical_memory_store.record_accesses(
                    snapshot_id=snapshot.snapshot_id,
                    items=canonical_memory_items,
                    purpose=f"context_assembler:{intent}",
                    created_at=snapshot.created_at,
                    user_authorized=user_authorized,
                )
            except Exception:
                # The durable Why Drawer audit is required when this optional
                # source is available, but an I/O fault must not make a prompt
                # assembly path unavailable.  Mark the immutable result so a
                # caller can surface the degradation instead of hiding it.
                _logger.exception("context_assembler canonical memory audit failed")
                snapshot = snapshot.model_copy(
                    update={
                        "degraded": True,
                        "degradation_reason": self._append_reason(
                            snapshot.degradation_reason,
                            "canonical_memory_access_log_failed",
                        ),
                    }
                )
        return snapshot

    def _read_subject_learning_state_snapshot(
        self,
        *,
        user_id: str,
        subject_id: str,
    ) -> SubjectLearningStateSnapshot | None:
        """Read a deterministic canonical ledger projection for one subject.

        There is intentionally no derived snapshot file: the immutable ledger
        plus versioned shared parameters are sufficient to reproduce the
        result, while a second persisted state would need its own amendment and
        replay protocol. An absent ledger is represented as an empty snapshot
        without creating runtime directories from this read path.
        """
        if self._subject_learning_state_snapshot_factory is not None:
            snapshot = self._subject_learning_state_snapshot_factory(user_id, subject_id)
        else:
            from traittutor.learning_governance.repository import (
                build_subject_learning_state_snapshot,
            )
            from traittutor.learning_model.events import LearnerEventLedger
            from traittutor.multi_user.context import get_current_user_or_none
            from traittutor.multi_user.paths import get_path_service_for_scope

            current_user = get_current_user_or_none()
            # A caller-provided owner is never sufficient authority. Older
            # direct/unit callers may not have installed an authenticated
            # request context, so skip this optional read rather than probing
            # the local-admin workspace or emitting a false degradation.
            if current_user is None or current_user.id != user_id:
                return None
            scope_path_service = get_path_service_for_scope(current_user.scope)
            workspace = scope_path_service.get_workspace_dir()
            ledger_path = workspace / "learning_model" / "learner_events.json"
            # The ledger persists to the unified database (Phase 5 task 10), so
            # its existence is now governed by that database rather than the
            # retired legacy JSON file. Do not create state merely to assemble
            # context when no immutable ledger exists yet; an in-memory empty
            # ledger is equivalent.
            unified_db = scope_path_service.get_traittutor_database_path()
            ledger = (
                LearnerEventLedger(ledger_path, path_service=scope_path_service)
                if unified_db.exists()
                else LearnerEventLedger()
            )
            snapshot = build_subject_learning_state_snapshot(
                owner_id=user_id,
                subject_id=subject_id,
                event_ledger=ledger,
            )
        if snapshot.owner_id != user_id or snapshot.subject_id != subject_id:
            raise PermissionError("subject learning snapshot ownership mismatch")
        return snapshot

    @staticmethod
    def _apply_subject_learning_state_context(
        context: PersonalizationContext,
        snapshot: SubjectLearningStateSnapshot,
    ) -> PersonalizationContext:
        """Add a bounded, answer-free canonical evidence envelope to prompts.

        KC ids, raw correctness, BKT posterior, answer keys, rubrics, legacy
        progress, and review schedules remain outside prompt context. The
        live personalization service keeps responsibility for selecting actual
        teaching actions; this envelope only proves how much strong canonical
        evidence was available for the current authoritative subject.
        """
        from traittutor.personalization.models import PersonalizationContext

        if not isinstance(context, PersonalizationContext):
            context = PersonalizationContext.model_validate(context)
        payload = {
            "subject_id": snapshot.subject_id,
            "source_revision": snapshot.source_revision,
            "strong_event_count": snapshot.strong_event_count,
            "observed_kc_count": len(snapshot.knowledge),
            "calibrated": snapshot.calibrated,
            "param_version": snapshot.param_version,
        }
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        # Keep an identity-bound provenance envelope even when pre-existing
        # personalization constraints consume the historical budget. This is
        # constructed wholly from server-owned typed data, never learner text.
        return context.model_copy(
            update={
                "constraints": list(
                    dict.fromkeys(
                        [f"canonical_subject_evidence={serialized}", *context.constraints]
                    )
                )[:7]
            }
        )

    def _read_tutor_persona_context(self, *, user_id: str) -> TutorPersonaContext | None:
        """Read one owner-local persona attachment without creating a default.

        Online generation is read-only: an absent or disabled persona must not
        become a persisted default merely because a prompt is being assembled.
        """
        from traittutor.tutor_persona.context_adapter import TutorPersonaContextAdapter
        from traittutor.tutor_persona.store import TutorPersonaStore

        store = (
            self._tutor_persona_store_factory(user_id)
            if self._tutor_persona_store_factory is not None
            else TutorPersonaStore(user_id)
        )
        if store.owner_id != user_id:
            raise PermissionError("tutor persona store does not own the context user")
        profile = store.get_current()
        if profile is None:
            return None
        if profile.owner_id != user_id:
            raise PermissionError("tutor persona profile does not own the context user")
        return TutorPersonaContextAdapter.adapt(profile)

    @staticmethod
    def _apply_tutor_persona_context(
        context: PersonalizationContext,
        persona: TutorPersonaContext,
    ) -> PersonalizationContext:
        """Pass only an allowlisted expression contract to live prompt input.

        ``constraints`` is already the bounded transport used by every live
        generation surface. Its record has a profile-version pointer, contract
        hash, and closed expression enums only. Identity, modality, quiet
        hours, accessibility, answers, BKT/KC, and safety state are excluded.
        """
        from traittutor.personalization.models import PersonalizationContext

        if not isinstance(context, PersonalizationContext):
            context = PersonalizationContext.model_validate(context)
        # ``persona_id`` is generated server-side today, but profile references
        # still cross a persistence boundary before reaching prompt context.
        # Treat malformed metadata as unavailable rather than relying on that
        # implementation detail for injection safety.
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,96}:v[1-9][0-9]*", persona.profile_ref):
            raise ValueError("tutor persona profile reference is not prompt-safe")
        if not re.fullmatch(r"[0-9a-f]{64}", persona.contract_hash):
            raise ValueError("tutor persona contract hash is not prompt-safe")
        payload = {
            "profile_ref": persona.profile_ref,
            "contract_hash": persona.contract_hash,
            "expression": persona.contract.expression.model_dump(mode="json"),
        }
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        expression_constraint = f"tutor_persona_expression={serialized}"
        return context.model_copy(
            update={
                # Keep the expression attachment available when existing
                # valid constraints have filled their bounded budget. It may
                # change wording only; it never changes teaching state.
                "constraints": list(dict.fromkeys([expression_constraint, *context.constraints]))[
                    :6
                ]
            }
        )

    def _read_canonical_memory(
        self,
        *,
        user_id: str,
        subject_id: str | None,
        project_id: str | None,
        purpose: str,
        memory_query: str | None,
        limit: int,
        token_budget: int,
    ) -> tuple[MemoryStore, list[UserMemoryItem], list[str]]:
        """Read owner-local current partitions plus targets of exact active grants.

        Global and exact current-subject behavior remains the safe default.
        Project-local memory is also available only for the exact current
        project. Cross-project, cross-subject, research, conversation, and KC
        partitions are consumed only through owner-bound, unexpired grants
        whose requesting partition and purpose exactly match this assembly.
        """
        from traittutor.memory.store import MemoryAuthorizationError, MemoryStore

        store = (
            self._canonical_memory_store_factory(user_id)
            if self._canonical_memory_store_factory is not None
            else MemoryStore(user_id)
        )
        if store.owner_id != user_id:
            raise PermissionError("canonical memory store does not own the context user")

        # The store enforces owner/status/validity.  Its public search API does
        # not represent an exact-None domain filter, so the remaining partition
        # checks live here and fail closed for malformed scoped records.
        global_items = [
            item
            for item in store.authorized_candidates(scope="global")
            if item.subject_id is None and item.kc_id is None and item.sensitivity != "sensitive"
        ]
        subject_items: list[UserMemoryItem] = []
        if subject_id is not None:
            subject_items = [
                item
                for item in store.authorized_candidates(scope="subject", subject_id=subject_id)
                if item.subject_id == subject_id
                and item.kc_id is None
                and item.sensitivity != "sensitive"
            ]

        project_items: list[UserMemoryItem] = []
        if project_id is not None:
            project_items = [
                item
                for item in store.authorized_candidates(scope="project", scope_id=project_id)
                if item.scope_id == project_id
                and item.subject_id in {None, subject_id}
                and item.kc_id is None
                and item.sensitivity != "sensitive"
            ]

        requesting_partitions: list[
            tuple[Literal["project", "subject"], str | None, str | None]
        ] = []
        if subject_id is not None:
            requesting_partitions.append(("subject", None, subject_id))
        if project_id is not None:
            requesting_partitions.append(("project", project_id, subject_id))

        granted_items: list[UserMemoryItem] = []
        for requesting_scope, requesting_scope_id, requesting_subject_id in requesting_partitions:
            grants = store.grants_for_request(
                requesting_scope=requesting_scope,
                requesting_scope_id=requesting_scope_id,
                requesting_subject_id=requesting_subject_id,
                requesting_kc_id=None,
                purpose=purpose,
            )
            for grant in grants:
                try:
                    granted = store.search_with_grant(
                        grant.grant_id,
                        requesting_scope=requesting_scope,
                        requesting_scope_id=requesting_scope_id,
                        requesting_subject_id=requesting_subject_id,
                        requesting_kc_id=None,
                        purpose=purpose,
                    )
                except MemoryAuthorizationError:
                    continue
                granted_items.extend(item for item in granted if item.sensitivity != "sensitive")

        # Current-subject values take precedence for the bounded prompt view;
        # references remain sorted later, independent of this precedence.  Do
        # not audit records beyond the snapshot's reference budget.
        candidates: list[UserMemoryItem] = []
        seen_memory_ids: set[str] = set()
        for item in subject_items + project_items + granted_items + global_items:
            if item.memory_id in seen_memory_ids:
                continue
            seen_memory_ids.add(item.memory_id)
            candidates.append(item)

        reasons: list[str] = []
        query = (memory_query or "").strip()
        if len(query) > 500:
            query = query[:500]
            reasons.append("canonical_memory_query_trimmed")
        result = store.rank_candidates(
            candidates,
            keyword=query or None,
            enable_vector=bool(query),
            limit=min(max(0, limit), 6),
            token_budget=max(0, token_budget),
        )
        reasons.extend(result.degradation_reasons)
        if result.trimmed_count:
            reasons.append(f"canonical_memory_budget_trimmed:{result.trimmed_count}")
        return store, list(result.items), list(dict.fromkeys(reasons))

    @staticmethod
    def _apply_canonical_memory_context(
        context: PersonalizationContext,
        items: Sequence[UserMemoryItem],
    ) -> tuple[list[MemoryRef], PersonalizationContext, list[str]]:
        """Turn a tiny safe subset of canonical memory into prompt context.

        Snapshot contracts retain ids and content versions only.  Raw memory
        reaches the prompt-facing personalization object only for recognized
        preference keys, after the same injection guard used for learner
        material.  Unknown keys still receive an auditable reference but have
        no prompt effect.  This protects the assistant from treating a generic
        note, research claim, or imperative text as a system instruction.
        """
        from traittutor.learning.intent import scan_untrusted_learning_payload
        from traittutor.personalization.models import PersonalizationContext

        if not isinstance(context, PersonalizationContext):
            context = PersonalizationContext.model_validate(context)

        refs = [
            MemoryRef(
                scope=f"canonical_memory:{item.scope}",
                key=item.memory_id,
                version=item.updated_at,
            )
            for item in items[:6]
        ]
        active_goal = context.active_goal
        constraints = list(context.constraints[:6])
        reasons: list[str] = []
        allowed_goal_keys = {"goal", "learning_goal"}
        allowed_constraint_keys = {
            "constraint",
            "explanation",
            "feedback",
            "pacing",
            "preference",
        }

        for item in items[:6]:
            normalized_key = item.key.strip().casefold().replace("-", "_").replace(" ", "_")
            if normalized_key not in allowed_goal_keys | allowed_constraint_keys:
                continue
            # Keep the prompt slice bounded rather than silently truncating a
            # potentially meaningful preference.  The item still appears as a
            # reference/audit row, allowing the learner to inspect why it was
            # not applied to this turn.
            if len(item.value) > 240:
                reasons.append("canonical_memory_content_rejected")
                continue
            action, _category = scan_untrusted_learning_payload(item.value)
            if action == "block":
                reasons.append("canonical_memory_content_rejected")
                continue
            if normalized_key in allowed_goal_keys:
                if active_goal is None:
                    active_goal = item.value
            elif len(constraints) < 6:
                constraints.append(f"{normalized_key}: {item.value}")

        updated = context.model_copy(
            update={
                "active_goal": active_goal,
                "constraints": list(dict.fromkeys(constraints)),
            }
        )
        return refs, updated, list(dict.fromkeys(reasons))

    def _read_personalization_context(
        self,
        *,
        intent: AssistantIntent,
        subject_id: str | None,
        user_id: str,
    ) -> tuple[PersonalizationContext, str]:
        """Call the canonical bounded builder without creating session state.

        ``build_context`` already applies evidence gates and selects at most a
        few relevant concept signals.  Passing an empty session id avoids its
        session persistence path, keeping this assembler a read-only boundary.
        Profile versions are content hashes rather than timestamps so an empty
        cold-start profile remains deterministic across repeated reads.
        """
        from traittutor.personalization.service import PersonalizationService

        # A fresh stateless facade avoids the singleton accessor's background
        # reconcile recovery, which is a write-side concern and must never be
        # triggered by deterministic prompt assembly.
        service = PersonalizationService()
        profile = (
            service.subject_profile(subject_id)
            if subject_id is not None
            else service.global_profile()
        )
        if profile.owner_id != user_id:
            raise PermissionError("context user does not own the learner profile")
        profile_payload = profile.model_dump(mode="json", exclude={"updated_at"})
        profile_version = (
            f"profile-v{profile.schema_version}-{_stable_digest(profile_payload)[:16]}"
        )
        purpose: Literal["chat", "courseware"] = (
            "courseware" if intent in {"learn", "create"} else "chat"
        )
        context = service.build_context(
            purpose=purpose,
            subject=profile.subject if subject_id is not None else None,
            session_id="",
        )
        return context, profile_version

    @staticmethod
    def _personalization_refs(
        context: PersonalizationContext,
    ) -> tuple[MemoryRef | None, list[ConceptSignalRef], str]:
        """Reference existing personalization models without cloning them."""
        from traittutor.personalization.models import (
            ConceptSignal,
            LearnerMemorySnapshot,
            PersonalizationContext,
        )

        if not isinstance(context, PersonalizationContext):
            context = PersonalizationContext.model_validate(context)
        memory = context.memory_snapshot
        memory_ref: MemoryRef | None = None
        if memory is not None:
            if not isinstance(memory, LearnerMemorySnapshot):
                memory = LearnerMemorySnapshot.model_validate(memory)
            memory_ref = MemoryRef(
                scope="learner_memory_snapshot",
                key=memory.snapshot_id,
                version=str(memory.version),
            )

        concept_refs: list[ConceptSignalRef] = []
        for signal in context.relevant_concept_signals:
            if not isinstance(signal, ConceptSignal):
                signal = ConceptSignal.model_validate(signal)
            version = f"concept-{_stable_digest(signal.model_dump(mode='json'))[:16]}"
            concept_refs.append(ConceptSignalRef(concept_id=signal.concept_id, version=version))
        plan_ref = f"teaching-plan-{_stable_digest(context.plan.model_dump(mode='json'))[:16]}"
        return memory_ref, concept_refs, plan_ref

    def _read_conversation_context(
        self,
        thread_id: str,
        *,
        user_id: str,
        subject_id: str | None,
        project_id: str | None,
    ) -> ConversationRetrievalResult:
        """Retrieve only exact-scope, active-branch safe episode summaries.

        The retrieval service resolves a session id to its owner-bound durable
        thread, rejects scope mismatches, and screens bounded L2 summaries.
        The immutable snapshot receives ids/versions only; the request-local
        personalization attachment is the sole prompt-facing consumer.
        """
        from traittutor.conversation.retrieval import ConversationRetrievalService

        service = (
            self._conversation_retrieval_service_factory(user_id)
            if self._conversation_retrieval_service_factory is not None
            else ConversationRetrievalService(user_id)
        )
        if service.owner_id != user_id:
            raise PermissionError("conversation retrieval service does not own the context user")
        return service.retrieve(
            thread_id,
            subject_id=subject_id,
            project_id=project_id,
        )

    @staticmethod
    def _apply_conversation_context(
        context: PersonalizationContext,
        conversation: ConversationRetrievalResult,
    ) -> PersonalizationContext:
        """Attach one bounded episode envelope as data, never instructions.

        ``ConversationRetrievalService`` has already applied ownership,
        branch, status, sensitivity, and injection checks.  We still encode a
        closed JSON envelope with only ids, summary versions, task labels, and
        compact L2 summaries so prompt consumers cannot mistake it for a full
        transcript, answer key, or executable directive.
        """
        from traittutor.personalization.models import PersonalizationContext

        if not isinstance(context, PersonalizationContext):
            context = PersonalizationContext.model_validate(context)
        payload = {
            "thread_id": conversation.thread_id,
            "episodes": [
                {
                    "episode_id": item.episode_id,
                    "summary_version": item.summary_version,
                    "task_type": item.task_type,
                    "summary": item.summary,
                }
                for item in conversation.episodes
            ],
        }
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return context.model_copy(
            update={
                "constraints": list(
                    dict.fromkeys(
                        [f"conversation_episode_context={serialized}", *context.constraints]
                    )
                )[:7]
            }
        )

    @staticmethod
    def _derive_trace_id(
        *,
        intent: AssistantIntent,
        user_id: str,
        subject_id: str | None,
        thread_id: str | None,
        prompt_bundle_id: str | None,
        read_ranges: SnapshotReadRanges,
    ) -> str:
        seed = {
            "intent": intent,
            "user_id": user_id,
            "subject_id": subject_id,
            "thread_id": thread_id,
            "prompt_bundle_id": prompt_bundle_id,
            "read_ranges": read_ranges.model_dump(mode="json", exclude_none=False),
        }
        return f"trace_{_stable_digest(seed)[:20]}"

    def _record_accesses(
        self,
        *,
        snapshot: AssistantContextSnapshot,
        memory_refs: Sequence[MemoryRef],
        concept_refs: Sequence[ConceptSignalRef],
        learner_profile_version: str | None,
        learner_profile_key: str,
        tutor_persona_ref: TutorPersonaRef | None,
        subject_learning_state_ref: SubjectLearningStateRef | None,
        purpose: str,
        user_authorized: bool,
        persist_cross_domain_to: MemoryStore | None = None,
    ) -> None:
        access_refs = [(item.scope, item.key, item.version) for item in memory_refs] + [
            ("subject_state", item.concept_id, item.version) for item in concept_refs
        ]
        if learner_profile_version is not None:
            access_refs.append(("learner_profile", learner_profile_key, learner_profile_version))
        if tutor_persona_ref is not None:
            access_refs.append(
                (
                    "tutor_persona",
                    tutor_persona_ref.profile_ref,
                    tutor_persona_ref.contract_hash,
                )
            )
        if subject_learning_state_ref is not None:
            access_refs.append(
                (
                    "canonical_subject_state",
                    f"{subject_learning_state_ref.owner_id}:{subject_learning_state_ref.subject_id}",
                    subject_learning_state_ref.source_revision,
                )
            )
        # Cross-domain scopes have no durable audit of their own; invariant 7
        # requires the Why Drawer trail to survive a restart. Memory scopes
        # are audited durably by ``record_accesses`` on the same store.
        cross_domain_scopes = {
            "subject_state",
            "learner_profile",
            "tutor_persona",
            "canonical_subject_state",
        }
        cross_domain_records: list[MemoryAccessRecord] = []
        for scope, key, version in access_refs:
            seed = {
                "snapshot_id": snapshot.snapshot_id,
                "scope": scope,
                "key": key,
                "version": version,
                "purpose": purpose,
                "user_authorized": user_authorized,
            }
            record = MemoryAccessRecord(
                record_id=f"mar_{_stable_digest(seed)[:20]}",
                snapshot_id=snapshot.snapshot_id,
                created_at=snapshot.created_at,
                scope=scope,
                key=key,
                version_read=version,
                purpose=purpose,
                user_authorized=user_authorized,
            )
            self.access_log.append(record)
            if scope in cross_domain_scopes:
                cross_domain_records.append(record)
        if persist_cross_domain_to is not None and cross_domain_records:
            persist_cross_domain_to.record_external_accesses(cross_domain_records)

    @staticmethod
    def _append_reason(current: str | None, reason: str) -> str:
        parts = list(dict.fromkeys(filter(None, (current or "").split(";") + [reason])))
        return ";".join(parts)
