"""Per-user learner-model storage and deterministic teaching-plan selection."""
# ruff: noqa: E701, E702 - preserve the existing compact live-path implementation

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import hashlib
import json
import logging
from pathlib import Path
import re
from typing import Any, Literal, Mapping, cast
from uuid import uuid4

from traittutor.assessment.big_five import build_initial_slr_support, list_trait_profiles
from traittutor.learning_model.parameters import (
    DEFAULT_PARAMS,
    BKTParamSet,
    get_active_bkt_params,
)
from traittutor.multi_user.context import get_current_user
from traittutor.services.path_service import get_path_service
from traittutor.unified_storage import SectionedRecordStore

from .models import (
    ConceptSignal,
    LearnerEvent,
    LearnerMemorySnapshot,
    LearnerProfile,
    LearningSignal,
    PersonalizationContext,
    PreferenceEvidence,
    ReflectionView,
    StrategyEvidence,
    SubjectRef,
    SubjectUnderstanding,
    TeachingAction,
    TeachingStrategyPlan,
    VisibleRationale,
)

_locks: dict[str, asyncio.Lock] = {}
_reconcile_tasks: dict[str, asyncio.Task[object]] = {}
_INFERENCE_TTL = timedelta(days=90)
# Canonical answer events are projected from the immutable learner-event ledger
# and use the exact parameter source that rebuilds that ledger.
CANONICAL_BKT_PARAM_VERSION = DEFAULT_PARAMS.version
CANONICAL_BKT_CALIBRATED = DEFAULT_PARAMS.calibrated
_RULE_SUBJECTS = (
    # Order matters: the first token found in the material wins, so put the
    # more specific token before the broader one (e.g. "微积分" before "数学").
    ("python", "Computer Science", "computer-science", "Python"),
    ("algorithm", "Computer Science", "computer-science", "Algorithms"),
    ("算法", "Computer Science", "computer-science", "Algorithms"),
    ("statistics", "Mathematics", "mathematics", "Statistics"),
    ("统计", "Mathematics", "mathematics", "Statistics"),
    ("english", "Languages", "languages", "English"),
    ("英语", "Languages", "languages", "English"),
    ("心理", "Psychology", "psychology", "Psychology"),
    ("physics", "Science", "science", "Physics"),
    ("物理", "Science", "science", "Physics"),
    ("微积分", "Mathematics", "mathematics", "Calculus"),
    ("高等数学", "Mathematics", "mathematics", "Calculus"),
    ("三角函数", "Mathematics", "mathematics", "Trigonometry"),
    ("代数", "Mathematics", "mathematics", "Algebra"),
    ("方程", "Mathematics", "mathematics", "Equations"),
    ("函数", "Mathematics", "mathematics", "Functions"),
    ("几何", "Mathematics", "mathematics", "Geometry"),
    ("数学", "Mathematics", "mathematics", "General"),
    ("化学", "Science", "science", "Chemistry"),
    ("生物", "Science", "science", "Biology"),
    ("历史", "History", "history", "History"),
    ("地理", "Geography", "geography", "Geography"),
    ("经济", "Economics", "economics", "Economics"),
    ("语文", "Languages", "languages", "Chinese"),
    ("文学", "Literature", "literature", "Literature"),
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _slug(value: str) -> str:
    return re.sub(r"[^\w]+", "-", value.lower(), flags=re.UNICODE).strip("-") or "unclassified"


def _safe_subject_id(value: str) -> str:
    """Keep user-controlled subject ids inside the owner-scoped learner root."""
    subject_id = value.strip()
    if not re.fullmatch(r"[\w.-]{1,100}", subject_id, flags=re.UNICODE) or subject_id in {
        ".",
        "..",
    }:
        raise ValueError("invalid subject id")
    return subject_id


def _lock(key: str) -> asyncio.Lock:
    return _locks.setdefault(key, asyncio.Lock())


def _canonical_bkt_params(event: LearnerEvent) -> BKTParamSet | None:
    """Return the shared ledger parameters for an explicit canonical projection.

    ``record_event`` also serves non-mastery personalization flows. The marker is emitted only by
    ``CanonicalAnswerEventChain`` after server-side grading; version text alone
    is not enough to silently reclassify an older event as canonical evidence.
    """
    if event.event_type != "mastery_attempt":
        return None
    if event.payload.get("canonical_bkt_projection") is not True:
        return None
    active_params = get_active_bkt_params()
    if event.payload.get("bkt_param_version") != active_params.version:
        return None
    return active_params


def _understanding(concepts: list[ConceptSignal]) -> SubjectUnderstanding | None:
    if not concepts:
        return None
    observed = [item for item in concepts if item.observation_count]
    verified = [item for item in concepts if item.verified_observation_count]
    calibrated = [item for item in verified if item.bkt_calibrated]
    mastery = (
        sum(item.mastery_probability for item in calibrated) / len(calibrated)
        if calibrated
        else None
    )
    verified_count = sum(item.verified_observation_count for item in concepts)
    confidence = min(
        1.0, sum(item.verified_observation_count for item in concepts) / max(3, len(concepts) * 3)
    )
    status: Literal["starting", "learning", "familiar", "verified"]
    if not verified:
        status = "starting"
    elif mastery is not None and mastery >= 0.78 and confidence >= 0.65:
        status = "verified"
    elif mastery is not None and mastery >= 0.58:
        status = "familiar"
    else:
        status = "learning"
    recent = max(
        (item.last_practised_at for item in concepts if item.last_practised_at), default=None
    )
    return SubjectUnderstanding(
        status=status,
        concept_count=len(concepts),
        observed_concept_count=len(observed),
        coverage=len(observed) / len(concepts),
        verified_mastery=mastery,
        verified_observation_count=verified_count,
        mastery_interval=(0.0, 1.0),
        confidence=confidence,
        recent_activity_at=recent,
        review_load=sum(1 for item in concepts if item.support_level == "needs_support"),
    )


logger = logging.getLogger(__name__)


class PersonalizationService:
    def __init__(self) -> None:
        # (resolved root, owner) -> adapter. Keyed by owner too: the adapter
        # carries the workspace owner id, and tests share one tmp root across
        # different owners.
        self._adapter_cache: dict[tuple[Path, str], SectionedRecordStore] = {}

    def _root(self) -> Path:
        return get_path_service().get_memory_dir() / "learner"

    def _global_path(self) -> Path:
        return self._root() / "global.json"

    def _subject_path(self, subject_id: str) -> Path:
        return self._root() / "subjects" / f"{_safe_subject_id(subject_id)}.json"

    def _signals_path(self) -> Path:
        return self._root() / "signals" / f"{datetime.now(UTC).strftime('%Y-%m')}.jsonl"

    def _sessions_path(self) -> Path:
        return self._root() / "sessions.json"

    def _jobs_path(self) -> Path:
        return self._root() / "jobs.json"

    def _owner(self) -> str:
        return get_current_user().id

    def _adapter(self) -> SectionedRecordStore:
        root = self._root().resolve()
        owner = self._owner()
        cached = self._adapter_cache.get((root, owner))
        if cached is not None:
            return cached
        path_service = get_path_service()
        canonical_root = path_service.get_memory_dir() / "learner"
        adapter = SectionedRecordStore(
            "personalization_state",
            owner,
            schema_version=1,
            path_service=path_service if root == canonical_root.resolve() else None,
            db_path=None if root == canonical_root.resolve() else root / "traittutor.sqlite3",
        )
        # ``SectionedRecordStore`` holds no cross-call state besides a
        # per-context transaction stack, so one instance per
        # (service, root, owner) is safe. Reconcile touches the adapter many
        # times per call — a fresh store (with its own create-tables
        # transaction) per access was pure overhead, and tests that repoint
        # ``_root`` get a new cache entry keyed by the resolved path.
        self._adapter_cache[(root, owner)] = adapter
        return adapter

    def _profile_key(self, path: Path) -> str:
        return "global" if path.name == "global.json" else f"subject:{path.stem}"

    def _sessions(self) -> dict[str, dict[str, Any]]:
        return {
            str(item["session_id"]): {
                key: value for key, value in item.items() if key != "session_id"
            }
            for item in self._adapter().snapshot()["sessions"]
            if item.get("owner_id") == self._owner() and item.get("session_id")
        }

    def _write_sessions(self, sessions: Mapping[str, Mapping[str, Any]]) -> None:
        rows = [
            {"session_id": session_id, "owner_id": self._owner(), **dict(state)}
            for session_id, state in sessions.items()
        ]
        # Sessions are fully derived here: rewrite only that section.
        self._adapter().replace_section("sessions", rows)

    def _default_profile(
        self, scope: Literal["global", "subject"], subject: SubjectRef | None = None
    ) -> LearnerProfile:
        return LearnerProfile(
            owner_id=self._owner(), scope=scope, subject=subject, updated_at=_now()
        )

    def _read_profile(
        self, path: Path, scope: Literal["global", "subject"], subject: SubjectRef | None = None
    ) -> LearnerProfile:
        key = self._profile_key(path)
        record = next(
            (
                item
                for item in self._adapter().snapshot()["profiles"]
                if item.get("storage_key") == key
            ),
            None,
        )
        if record is None:
            return self._default_profile(scope, subject)
        profile = LearnerProfile.model_validate(record["profile"])
        if profile.owner_id != self._owner():
            raise PermissionError("learner profile ownership mismatch")
        return profile

    def _write_profile(self, path: Path, profile: LearnerProfile) -> None:
        adapter = self._adapter()
        key = self._profile_key(path)
        record = {
            "storage_key": key,
            "owner_id": self._owner(),
            "profile": profile.model_dump(
                mode="json", context={"include_uncalibrated_posterior": True}
            ),
        }
        # Rewrite only the profiles section: sibling sections (signals,
        # sessions, jobs) must not pay for a profile update.
        profiles = [
            item for item in adapter.read_section("profiles") if item.get("storage_key") != key
        ]
        profiles.append(record)
        adapter.replace_section("profiles", profiles)

    def classify_subject(
        self, *, material_analysis: Mapping[str, Any] | None = None, title: str = "", text: str = ""
    ) -> SubjectRef | None:
        analysis = dict(material_analysis or {})
        raw_subject = str(analysis.get("subject") or analysis.get("big_subject") or "").strip()
        sub = str(analysis.get("sub_subject") or analysis.get("small_subject") or "").strip()
        confidence = float(analysis.get("confidence") or 0) if raw_subject else 0
        if raw_subject:
            return SubjectRef(
                subject_id=_slug(raw_subject),
                label=raw_subject,
                path=[raw_subject, *([sub] if sub else [])],
                confidence=min(1, confidence),
                source="material_analysis",
                confirmed=False,
            )
        haystack = f"{title} {text[:3000]}".lower()
        for token, label, subject_id, topic in _RULE_SUBJECTS:
            if token in haystack:
                return SubjectRef(
                    subject_id=subject_id,
                    label=label,
                    path=[label, topic],
                    confidence=0.66,
                    source="rule",
                    confirmed=False,
                )
        return None

    async def record_event(
        self, event: LearnerEvent, *, trusted: bool = False
    ) -> list[LearnerProfile]:
        """Persist one normalized event and synchronously refresh its read model.

        The event is represented by the existing append-only signal audit so
        legacy evidence controls and deterministic rebuilds remain one system.
        """
        # Only server-owned study flows may create observations that change
        # verified mastery.  A browser can still submit an explicit self-report
        # or correction, but it must never be able to masquerade as a graded
        # quiz/mastery result simply by choosing an event_type.
        if not trusted and event.event_type not in {
            "self_assessment",
            "chat_correction",
            "strategy_feedback",
        }:
            raise PermissionError("event type must be recorded by a trusted learning flow")
        if event.event_type in {"mastery_attempt", "quiz_answer", "flashcard_review"} and (
            event.event_type != "mastery_attempt"
            or event.payload.get("canonical_bkt_projection") is not True
        ):
            raise ValueError("mastery evidence must come from the canonical answer-event chain")
        signal = self._signal_from_event(event)
        return await self.apply_signal(signal)

    def _signal_from_event(self, event: LearnerEvent) -> LearningSignal:
        """Normalize a server event without persisting or applying it."""
        payload = {
            **event.payload,
            "event_type": event.event_type,
            "concept": event.concept_label or event.concept_id or "",
            "concept_id": event.concept_id or "",
            "module_id": event.module_id or "",
            "observation": event.observation,
            "event_confidence": event.confidence,
        }
        if event.event_type in {
            "mastery_attempt",
            "quiz_answer",
            "flashcard_review",
            "self_assessment",
        }:
            payload["correct"] = event.observation in {"correct", "known"}
        kind: Literal["explicit_preference", "learner_event"] = (
            "explicit_preference" if event.event_type == "memory_preference" else "learner_event"
        )
        if kind == "explicit_preference":
            payload["value"] = str(
                event.payload.get("value") or event.payload.get("text") or ""
            ).strip()
            payload["category"] = str(event.payload.get("category") or "explanation")
        return LearningSignal(
            signal_id=event.event_id,
            kind=kind,
            subject_refs=[event.subject] if event.subject else [],
            payload=payload,
            evidence_refs=event.evidence_refs,
            source="user"
            if event.event_type in {"self_assessment", "memory_preference"}
            else "system",
            occurred_at=event.occurred_at,
        )

    async def replace_canonical_bkt_projections(self, events: tuple[LearnerEvent, ...]) -> int:
        """Atomically replace only ledger-derived BKT signals, then rebuild views."""
        owner = self._owner()
        active_params = get_active_bkt_params()
        signals: list[LearningSignal] = []
        for event in events:
            if event.event_type != "mastery_attempt":
                raise ValueError("BKT rebuild accepts only mastery_attempt projections")
            if event.payload.get("canonical_bkt_projection") is not True:
                raise ValueError("BKT rebuild event is missing its canonical marker")
            if event.payload.get("bkt_param_version") != active_params.version:
                raise ValueError("BKT rebuild event parameter version does not match activation")
            signals.append(self._signal_from_event(event).model_copy(update={"owner_id": owner}))
        if len({item.signal_id for item in signals}) != len(signals):
            raise ValueError("BKT rebuild produced duplicate projection ids")
        async with _lock(f"{owner}:mutation"):
            adapter = self._adapter()
            retained = [
                item
                for item in adapter.read_section("signals")
                if item.get("payload", {}).get("canonical_bkt_projection") is not True
            ]
            adapter.replace_section(
                "signals",
                [
                    *retained,
                    *(
                        item.model_dump(mode="json", exclude_none=True, by_alias=True)
                        for item in signals
                    ),
                ],
            )
            self._rebuild_profiles_locked()
        return len(signals)

    def record_event_background(self, event: LearnerEvent) -> None:
        """Best-effort runtime bridge; the primary learning action must not fail."""
        try:
            asyncio.get_running_loop().create_task(self.record_event(event, trusted=True))
        except RuntimeError:
            # Synchronous tests and command-line actions have no running loop.
            # Their canonical progress write remains valid; a later reconcile
            # pass can import it without inventing a result.
            return

    def memory_reconcile_status(self) -> dict[str, Any]:
        record = next(
            (
                item
                for item in self._adapter().snapshot()["jobs"]
                if item.get("job_id") == "memory-reconcile"
            ),
            None,
        )
        return (
            {key: value for key, value in record.items() if key not in {"job_id", "owner_id"}}
            if record
            else {"state": "idle", "last_completed_at": None, "error": None}
        )

    def _write_reconcile_status(self, value: Mapping[str, Any]) -> None:
        adapter = self._adapter()
        jobs = [
            item
            for item in adapter.read_section("jobs")
            if item.get("job_id") != "memory-reconcile"
        ]
        jobs.append({"job_id": "memory-reconcile", "owner_id": self._owner(), **dict(value)})
        adapter.replace_section("jobs", jobs)

    async def reconcile_memory(self) -> dict[str, Any]:
        """Project explicit canonical preferences into learner-support signals."""
        from traittutor.memory.runtime import get_current_memory_store

        owner = self._owner()
        async with _lock(f"{owner}:memory-reconcile"):
            self._write_reconcile_status({"state": "running", "started_at": _now(), "error": None})
            imported = 0
            try:
                entries = [
                    item
                    for item in get_current_memory_store(owner).list_items(status="active")
                    if item.provenance == "explicit" and item.key.startswith("preference:")
                ]

                def entry_event_id(entry: Any) -> str:
                    digest = hashlib.sha256(entry.updated_at.encode("utf-8")).hexdigest()[:16]
                    return f"memory-{entry.memory_id}-{digest}"

                valid_event_ids = {entry_event_id(entry) for entry in entries}
                # A canonical edit/delete invalidates the derived event;
                # remove it first so rebuild never silently preserves stale text.
                removed_stale = 0
                for signal in self._all_signals():
                    if (
                        signal.signal_id.startswith("memory-")
                        and signal.signal_id not in valid_event_ids
                    ):
                        await self.delete_evidence(signal.signal_id)
                        removed_stale += 1
                if removed_stale:
                    # Frozen per-session snapshots were curated from the now
                    # stale derived preferences. Drop them so the next
                    # build_context re-curates from canonical truth instead of
                    # replaying deleted/deactivated text (AGENTS.md invariant:
                    # removed information must not enter later snapshots).
                    self._invalidate_session_memory_snapshots()
                for entry in entries:
                    text = entry.value.strip()
                    if not text:
                        continue
                    event = LearnerEvent(
                        event_id=entry_event_id(entry),
                        event_type="memory_preference",
                        confidence=entry.confidence,
                        evidence_refs=[entry.memory_id, *entry.evidence_refs],
                        payload={"value": text, "category": "explanation"},
                        occurred_at=_now(),
                    )
                    before = len(self.evidence())
                    await self.record_event(event, trusted=True)
                    imported += int(len(self.evidence()) > before)
                result = {
                    "state": "completed",
                    "last_completed_at": _now(),
                    "imported": imported,
                    "error": None,
                }
                self._write_reconcile_status(result)
                return result
            except Exception as exc:
                result = {
                    "state": "failed",
                    "last_completed_at": None,
                    "imported": imported,
                    "error": type(exc).__name__,
                }
                self._write_reconcile_status(result)
                return result

    def enqueue_memory_reconcile(self) -> dict[str, Any]:
        status = self.memory_reconcile_status()
        if status.get("state") == "running" and self._owner() in _reconcile_tasks:
            return status
        attempts = int(status.get("attempts") or 0)
        self._write_reconcile_status(
            {"state": "queued", "queued_at": _now(), "error": None, "attempts": attempts}
        )
        try:
            owner = self._owner()
            task = asyncio.get_running_loop().create_task(self.reconcile_memory())
            _reconcile_tasks[owner] = task

            def _finished(done: asyncio.Task[object]) -> None:
                _reconcile_tasks.pop(owner, None)
                try:
                    if done.cancelled():
                        raise asyncio.CancelledError
                    done.result()
                except BaseException:
                    # An interrupted worker did not get a chance to persist its
                    # terminal status. Keep the job retryable. Normal failures
                    # are converted into a ``failed`` result by
                    # ``reconcile_memory`` and must not be overwritten here.
                    self._write_reconcile_status(
                        {
                            "state": "queued",
                            "queued_at": _now(),
                            "error": "worker_interrupted",
                            "attempts": attempts + 1,
                        }
                    )

            task.add_done_callback(_finished)
        except RuntimeError:
            pass
        return self.memory_reconcile_status()

    async def apply_signal(self, signal: LearningSignal) -> list[LearnerProfile]:
        """Idempotently append a validated signal and update its permitted read models."""
        owner = self._owner()
        if signal.owner_id not in {None, owner}:
            raise PermissionError("learner signal ownership mismatch")
        signal = signal.model_copy(update={"owner_id": owner})
        if signal.payload.get("canonical_bkt_projection") is True:
            # Resolve the active parameter artifact *before* appending: a
            # fail-closed config error here leaves no half-applied signal that
            # the idempotency gate below would otherwise turn into a permanent
            # no-op on retry (appended but never projected).
            get_active_bkt_params()
        profiles: list[LearnerProfile] = []
        # One owner-wide mutation lock makes append, delete/rebuild and profile
        # projection a single serialized transaction for the file store.
        async with _lock(f"{owner}:mutation"):
            # Idempotency is global to the learner audit, not just the current
            # month: queued retries can legitimately cross a month boundary.
            if any(existing.signal_id == signal.signal_id for existing in self._all_signals()):
                return profiles
            adapter = self._adapter()
            # Pure append (idempotent by ``signal_id``): no read-modify-
            # rewrite of the whole store for one new signal.
            adapter.append_records(
                "signals",
                [signal.model_dump(mode="json", exclude_none=True, by_alias=True)],
            )
        targets: list[SubjectRef | None] = list(signal.subject_refs) or [None]
        for subject in targets:
            scope: Literal["global", "subject"] = (
                "subject" if subject and subject.confidence >= 0.65 else "global"
            )
            path = (
                self._subject_path(subject.subject_id)
                if scope == "subject" and subject
                else self._global_path()
            )
            async with _lock(f"{owner}:{scope}:{subject.subject_id if subject else 'global'}"):
                profile = self._read_profile(path, scope, subject)
                profile = self._apply_to_profile(profile, signal)
                self._write_profile(path, profile)
                profiles.append(profile)
        return profiles

    def _apply_to_profile(self, profile: LearnerProfile, signal: LearningSignal) -> LearnerProfile:
        data = profile.model_dump(context={"include_uncalibrated_posterior": True})
        prefs = [PreferenceEvidence.model_validate(item) for item in data["preferences"]]
        if signal.kind == "reflection_decision":
            target_id = str(signal.payload.get("reflection_id") or "")
            decision = str(signal.payload.get("decision") or "")
            if target_id and decision in {"candidate", "confirmed", "rejected"}:
                state_by_decision = {
                    "candidate": "inferred",
                    "confirmed": "explicit",
                    "rejected": "rejected",
                }
                prefs = [
                    item.model_copy(
                        update={
                            "state": state_by_decision[decision],
                            "confidence": 1.0 if decision == "confirmed" else item.confidence,
                            "updated_at": _now(),
                            "expires_at": None
                            if decision == "confirmed"
                            else (
                                (datetime.now(UTC) + _INFERENCE_TTL).isoformat()
                                if decision == "candidate"
                                else item.expires_at
                            ),
                        }
                    )
                    if item.id == target_id
                    else item
                    for item in prefs
                ]
        if signal.kind in {"explicit_preference", "goal", "strategy_feedback"}:
            category_value = (
                "goal"
                if signal.kind == "goal"
                else str(signal.payload.get("category") or "explanation")
            )
            category = cast(
                Literal["goal", "explanation", "pacing", "feedback", "constraint"],
                category_value
                if category_value in {"goal", "explanation", "pacing", "feedback", "constraint"}
                else "explanation",
            )
            value = str(signal.payload.get("value") or signal.payload.get("text") or "").strip()
            if value:
                state: Literal["explicit", "inferred", "rejected"] = (
                    "explicit"
                    if signal.source == "user" and signal.kind != "strategy_feedback"
                    else ("rejected" if signal.payload.get("rejected") else "inferred")
                )
                prefs = [p for p in prefs if not (p.category == category and p.value == value)]
                prefs.append(
                    PreferenceEvidence(
                        id=signal.signal_id,
                        value=value,
                        category=category,
                        state=state,
                        confidence=1.0 if state == "explicit" else 0.8,
                        evidence_refs=signal.evidence_refs,
                        updated_at=_now(),
                        expires_at=None
                        if state == "explicit"
                        else (datetime.now(UTC) + _INFERENCE_TTL).isoformat(),
                    )
                )
        concepts = [ConceptSignal.model_validate(item) for item in data["concept_signals"]]
        if signal.kind in {"quiz_attempt", "misconception", "learner_event"}:
            concept = str(signal.payload.get("concept") or "").strip()
            concept_id = str(signal.payload.get("concept_id") or "").strip() or _slug(concept)
            if concept and concept_id:
                prior_concept = next(
                    (item for item in concepts if item.concept_id == concept_id), None
                )
                event_type = str(signal.payload.get("event_type") or "quiz_answer")
                observation = signal.payload.get("observation")
                event = LearnerEvent(
                    event_id=signal.signal_id,
                    event_type=cast(
                        Any,
                        event_type
                        if event_type
                        in {
                            "mastery_attempt",
                            "quiz_answer",
                            "flashcard_review",
                            "courseware_outcome",
                            "chat_correction",
                            "self_assessment",
                            "memory_preference",
                            "memory_candidate",
                            "strategy_feedback",
                        }
                        else "quiz_answer",
                    ),
                    concept_id=concept_id,
                    concept_label=concept,
                    module_id=str(signal.payload.get("module_id") or "") or None,
                    observation=observation
                    if observation
                    in {"correct", "incorrect", "known", "unknown", "uncertain", "engaged"}
                    else ("correct" if signal.payload.get("correct") else "incorrect"),
                    confidence=float(signal.payload.get("event_confidence") or 0.7),
                    evidence_refs=signal.evidence_refs,
                    # Keep the server-created projection marker and parameter
                    # version available to the policy selector below.  The
                    # normalized event otherwise intentionally contains only
                    # public identifiers/references, never an answer key.
                    payload=dict(signal.payload),
                    occurred_at=signal.occurred_at,
                )
                canonical_params = _canonical_bkt_params(event)
                if canonical_params is not None:
                    required_projection = {
                        "canonical_mastery_probability",
                        "canonical_initial_mastery_probability",
                        "canonical_verified_observation_count",
                    }
                    missing = required_projection.difference(signal.payload)
                    if missing:
                        raise ValueError(
                            "canonical BKT projection is incomplete: " + ", ".join(sorted(missing))
                        )
                    probability = float(signal.payload["canonical_mastery_probability"])
                    initial_mastery = float(signal.payload["canonical_initial_mastery_probability"])
                    verified_count = int(signal.payload["canonical_verified_observation_count"])
                    correct = event.observation == "correct"
                    # bkt-stage-policy-v1: the posterior influences product
                    # thresholds only after calibration and three strong
                    # observations. A current incorrect answer remains an
                    # explicit support need regardless of the posterior.
                    support: Literal["needs_support", "developing", "supported"]
                    if not correct:
                        support = "needs_support"
                    elif not canonical_params.calibrated or verified_count < 3:
                        support = "developing"
                    elif probability < 0.4:
                        support = "needs_support"
                    elif probability >= 0.75:
                        support = "supported"
                    else:
                        support = "developing"
                    replacement_concept = ConceptSignal(
                        concept_id=concept_id,
                        label=concept,
                        support_level=support,
                        confidence=min(
                            0.95, 0.25 + verified_count * 0.16 + event.confidence * 0.15
                        ),
                        attempt_count=verified_count,
                        misconception_tags=[]
                        if correct
                        else [str(signal.payload.get("misconception") or "needs review")],
                        evidence_refs=list(
                            dict.fromkeys(
                                (prior_concept.evidence_refs if prior_concept else [])
                                + signal.evidence_refs
                            )
                        ),
                        last_practised_at=_now(),
                        module_id=event.module_id
                        or (prior_concept.module_id if prior_concept else None),
                        mastery_probability=probability,
                        initial_mastery_probability=initial_mastery,
                        transition_probability=canonical_params.transition,
                        guess_probability=canonical_params.guess,
                        slip_probability=canonical_params.slip,
                        observation_count=verified_count,
                        verified_observation_count=verified_count,
                        last_observation_source=event.event_type,
                        bkt_param_version=canonical_params.version,
                        bkt_calibrated=canonical_params.calibrated,
                        mastery_interval=(0.0, 1.0),
                    )
                    concepts = [
                        item
                        for item in concepts
                        if item.concept_id != replacement_concept.concept_id
                    ] + [replacement_concept]
        strategies = [StrategyEvidence.model_validate(item) for item in data["strategy_evidence"]]
        inference_enabled = (
            profile.inference_enabled
            if profile.scope == "global"
            else self.global_profile().inference_enabled
        )
        if signal.kind in {"strategy_feedback", "artifact_outcome"} and inference_enabled:
            raw = signal.payload.get("strategy")
            if isinstance(raw, Mapping):
                strategy = TeachingAction.model_validate(raw)
                prior_strategy = next(
                    (
                        item
                        for item in strategies
                        if item.task_type == signal.payload.get("task_type", "chat")
                        and item.strategy == strategy
                    ),
                    None,
                )
                positive = (prior_strategy.positive_weight if prior_strategy else 0) + (
                    1 if signal.payload.get("positive") else 0
                )
                negative = (prior_strategy.negative_weight if prior_strategy else 0) + (
                    1 if signal.payload.get("negative") else 0
                )
                evidence = list(
                    dict.fromkeys(
                        (prior_strategy.evidence_refs if prior_strategy else [])
                        + signal.evidence_refs
                    )
                )
                event_ids = list(
                    dict.fromkeys(
                        (prior_strategy.event_ids if prior_strategy else []) + [signal.signal_id]
                    )
                )
                replacement_strategy = StrategyEvidence(
                    id=prior_strategy.id if prior_strategy else signal.signal_id,
                    strategy=strategy,
                    task_type=signal.payload.get("task_type", "chat"),
                    positive_weight=positive,
                    negative_weight=negative,
                    confidence=min(0.95, len(event_ids) / 3),
                    evidence_refs=evidence,
                    event_ids=event_ids,
                    last_observed_at=_now(),
                )
                strategies = [item for item in strategies if item.id != replacement_strategy.id] + [
                    replacement_strategy
                ]
        return LearnerProfile(
            owner_id=profile.owner_id,
            scope=profile.scope,
            subject=profile.subject,
            inference_enabled=profile.inference_enabled,
            preferences=prefs,
            concept_signals=concepts,
            strategy_evidence=strategies,
            understanding=_understanding(concepts) if profile.scope == "subject" else None,
            evidence_refs=list(dict.fromkeys(profile.evidence_refs + signal.evidence_refs)),
            schema_version=2,
            updated_at=_now(),
            needs_rebuild=False,
        )

    def set_inference(self, enabled: bool) -> LearnerProfile:
        path = self._global_path()
        profile = self._read_profile(path, "global")
        updated = profile.model_copy(update={"inference_enabled": enabled, "updated_at": _now()})
        self._write_profile(path, updated)
        return updated

    def global_profile(self) -> LearnerProfile:
        return self._read_profile(self._global_path(), "global")

    def subject_profile(self, subject_id: str) -> LearnerProfile:
        path = self._subject_path(subject_id)
        return self._read_profile(path, "subject")

    def reconcile_graph_concepts(self, subject: SubjectRef, nodes: list[Mapping[str, Any]]) -> None:
        """Replace early chunk-id BKT entries once a grounded graph is available.

        Generation and review can happen before the background graph build
        finishes.  In that window we retain the source chunk id rather than
        dropping a learning event; this deterministic reconciliation makes the
        later graph node the canonical BKT key without losing observations.
        """
        path = self._subject_path(subject.subject_id)
        candidates: dict[str, Mapping[str, Any]] = {}
        for node in nodes:
            for chunk_id in node.get("evidence_chunk_ids", []):
                current = candidates.get(str(chunk_id))
                if current is None or float(node.get("confidence") or 0) > float(
                    current.get("confidence") or 0
                ):
                    candidates[str(chunk_id)] = node
        if not candidates:
            return
        profile = self._read_profile(path, "subject")
        updated: dict[str, ConceptSignal] = {}
        changed = False
        for signal in profile.concept_signals:
            candidate_node = candidates.get(signal.concept_id)
            if candidate_node is None:
                updated[signal.concept_id] = signal
                continue
            concept_id = str(candidate_node.get("concept_id") or signal.concept_id)
            replacement = signal.model_copy(
                update={
                    "concept_id": concept_id,
                    "label": str(candidate_node.get("label") or signal.label),
                    "module_id": str(candidate_node.get("module_id") or signal.module_id or "")
                    or None,
                }
            )
            prior = updated.get(concept_id)
            if prior is not None:
                replacement = replacement.model_copy(
                    update={
                        "attempt_count": prior.attempt_count + replacement.attempt_count,
                        "observation_count": prior.observation_count
                        + replacement.observation_count,
                        "verified_observation_count": prior.verified_observation_count
                        + replacement.verified_observation_count,
                        "mastery_probability": max(
                            prior.mastery_probability, replacement.mastery_probability
                        ),
                        "confidence": max(prior.confidence, replacement.confidence),
                        "evidence_refs": list(
                            dict.fromkeys(prior.evidence_refs + replacement.evidence_refs)
                        ),
                    }
                )
            updated[concept_id] = replacement
            changed = changed or concept_id != signal.concept_id
        if changed:
            concepts = list(updated.values())
            self._write_profile(
                path,
                profile.model_copy(
                    update={
                        "concept_signals": concepts,
                        "understanding": _understanding(concepts),
                        "updated_at": _now(),
                    }
                ),
            )

    def subjects(self) -> list[LearnerProfile]:
        out = []
        for item in self._adapter().snapshot()["profiles"]:
            key = str(item.get("storage_key") or "")
            if not key.startswith("subject:"):
                continue
            try:
                profile = LearnerProfile.model_validate(item["profile"])
                if profile.owner_id != self._owner():
                    raise PermissionError("learner profile ownership mismatch")
                out.append(profile)
            except (KeyError, ValueError, PermissionError):
                continue
        return sorted(out, key=lambda item: item.updated_at, reverse=True)

    def overview(self) -> dict[str, Any]:
        global_profile = self.global_profile()
        subjects = self.subjects()
        return {
            # Keep typed profiles until the API boundary so its allowlisted
            # projection can apply decay before private numerics are omitted.
            "global": global_profile,
            "subjects": subjects,
            "inference_enabled": global_profile.inference_enabled,
            "pending_subjects": [
                item for item in subjects if item.subject and not item.subject.confirmed
            ],
            "memory_reconcile": self.memory_reconcile_status(),
            "reflection_summary": self.reflection_summary(),
        }

    def reflections(self, *, subject_id: str | None = None) -> list[ReflectionView]:
        from traittutor.learning_model.decay import project_concept_signal

        profiles = [self.global_profile(), *self.subjects()]
        if subject_id:
            profiles = [
                profile
                for profile in profiles
                if profile.subject and profile.subject.subject_id == subject_id
            ]
        out: list[ReflectionView] = []
        now = datetime.now(UTC)
        for profile in profiles:
            for preference in profile.preferences:
                expired = bool(
                    preference.expires_at and datetime.fromisoformat(preference.expires_at) <= now
                )
                status: Literal["candidate", "confirmed", "rejected", "stale", "needs_rebuild"]
                if profile.needs_rebuild:
                    status = "needs_rebuild"
                elif preference.state == "explicit":
                    status = "confirmed"
                elif preference.state == "rejected":
                    status = "rejected"
                elif expired:
                    status = "stale"
                else:
                    status = "candidate"
                out.append(
                    ReflectionView(
                        reflection_id=preference.id,
                        scope=profile.scope,
                        subject=profile.subject,
                        category=preference.category,
                        value=preference.value,
                        status=status,
                        source_state=preference.state,
                        confidence=preference.confidence,
                        evidence_refs=preference.evidence_refs,
                        updated_at=preference.updated_at,
                        expires_at=preference.expires_at,
                        applies_to_compass=status == "confirmed",
                        reason="已确认，会用于下一次生成。"
                        if status == "confirmed"
                        else (
                            "已拒绝，仅作为约束或审计记录。"
                            if status == "rejected"
                            else "候选记忆；确认前不会进入生成上下文。"
                        ),
                    )
                )
            for stored_concept in profile.concept_signals:
                concept = project_concept_signal(stored_concept)
                confirmed = concept.verified_observation_count > 0
                status = cast(
                    Literal["candidate", "confirmed", "rejected", "stale", "needs_rebuild"],
                    "needs_rebuild"
                    if profile.needs_rebuild
                    else ("confirmed" if confirmed else "candidate"),
                )
                out.append(
                    ReflectionView(
                        reflection_id=f"concept:{concept.concept_id}",
                        scope=profile.scope,
                        subject=profile.subject,
                        category="concept",
                        value=f"{concept.label} · {concept.support_level}",
                        status=status,
                        source_state=None,
                        confidence=concept.confidence,
                        evidence_refs=concept.evidence_refs,
                        updated_at=concept.last_practised_at or profile.updated_at,
                        applies_to_compass=confirmed and concept.support_level == "needs_support",
                        reason="来自可判分练习/复习，会用于安排薄弱概念。"
                        if confirmed
                        else "来自材料候选图谱，等待作答或复习证据确认。",
                    )
                )
        return sorted(out, key=lambda item: item.updated_at, reverse=True)[:120]

    def reflection_summary(self) -> dict[str, int]:
        summary = {
            "confirmed": 0,
            "candidate": 0,
            "rejected": 0,
            "stale": 0,
            "needs_rebuild": 0,
            "applies_to_compass": 0,
        }
        for reflection in self.reflections():
            summary[reflection.status] += 1
            summary["applies_to_compass"] += int(reflection.applies_to_compass)
        return summary

    async def decide_reflection(
        self, reflection_id: str, decision: Literal["candidate", "confirmed", "rejected"]
    ) -> ReflectionView | None:
        existing = next(
            (
                item
                for item in self.reflections()
                if item.reflection_id == reflection_id and item.category != "concept"
            ),
            None,
        )
        if existing is None:
            return None
        signal = LearningSignal(
            signal_id=f"reflection-{uuid4().hex}",
            kind="reflection_decision",
            subject_refs=[existing.subject] if existing.subject else [],
            payload={"reflection_id": reflection_id, "decision": decision},
            evidence_refs=list(dict.fromkeys([reflection_id, *existing.evidence_refs]))[:24],
            source="user",
            occurred_at=_now(),
        )
        await self.apply_signal(signal)
        return next(
            (item for item in self.reflections() if item.reflection_id == reflection_id), None
        )

    def clear_session_state(self, session_id: str) -> bool:
        """Delete only the current user's auxiliary learner session state."""
        sessions = self._sessions()
        if session_id not in sessions:
            return False
        sessions.pop(session_id, None)
        self._write_sessions(sessions)
        return True

    def _invalidate_session_memory_snapshots(self) -> int:
        """Drop frozen ``memory_snapshot`` frames derived from removed state.

        Snapshot content is a deterministic projection of the global learner
        profile, so dropping it is lossless: the next session turn simply
        re-curates from the rebuilt profile. Returns the number of sessions
        whose frozen frame was dropped.
        """
        sessions = self._sessions()
        invalidated = 0
        for session_id, state in list(sessions.items()):
            if isinstance(state, dict) and state.pop("memory_snapshot", None) is not None:
                invalidated += 1
                sessions[session_id] = state
        if invalidated:
            self._write_sessions(sessions)
        return invalidated

    def _all_signals(self) -> list[LearningSignal]:
        signals: list[LearningSignal] = []
        for record in self._adapter().snapshot()["signals"]:
            try:
                signal = LearningSignal.model_validate(record)
                if signal.owner_id in {None, self._owner()}:
                    signals.append(signal)
            except ValueError:
                continue
        return signals

    def evidence(self, *, subject_id: str | None = None) -> list[LearningSignal]:
        signals = self._all_signals()
        if subject_id:
            signals = [
                signal
                for signal in signals
                if any(ref.subject_id == subject_id for ref in signal.subject_refs)
            ]
        return sorted(signals, key=lambda item: item.occurred_at, reverse=True)[:100]

    async def delete_evidence(self, signal_id: str) -> bool:
        """Remove an audit event and deterministically rebuild affected views."""
        async with _lock(f"{self._owner()}:mutation"):
            return self._delete_evidence_locked(signal_id)

    def _delete_evidence_locked(self, signal_id: str) -> bool:
        """Delete/rebuild while the caller owns the owner mutation lock."""
        adapter = self._adapter()
        retained = []
        found = False
        for record in adapter.read_section("signals"):
            signal = LearningSignal.model_validate(record)
            if (
                signal.signal_id == signal_id
                or signal.payload.get("canonical_source_event_id") == signal_id
            ):
                found = True
            else:
                retained.append(record)
        if found:
            adapter.replace_section("signals", retained)
        if not found:
            return False
        self._rebuild_profiles_locked()
        return True

    def _rebuild_profiles_locked(self) -> None:
        """Reproject every profile from the remaining owner-scoped audit."""
        global_inference = self.global_profile().inference_enabled
        adapter = self._adapter()
        with adapter.locked() as payload:
            payload["profiles"] = []
            adapter.replace_all(payload)
        for signal in self._all_signals():
            # Rebuild without recursively appending audit lines.
            rebuild_targets: list[SubjectRef | None] = list(signal.subject_refs) or [None]
            for subject in rebuild_targets:
                scope: Literal["global", "subject"] = (
                    "subject" if subject and subject.confidence >= 0.65 else "global"
                )
                path = (
                    self._subject_path(subject.subject_id)
                    if scope == "subject" and subject
                    else self._global_path()
                )
                profile = self._read_profile(path, scope, subject)
                self._write_profile(path, self._apply_to_profile(profile, signal))
        profile = self.global_profile().model_copy(
            update={"inference_enabled": global_inference, "updated_at": _now()}
        )
        self._write_profile(self._global_path(), profile)

    async def correct_subject(self, old_subject_id: str, replacement: SubjectRef) -> LearnerProfile:
        """Reassign auditable evidence instead of creating a second subject silo."""
        owner = self._owner()
        async with _lock(f"{owner}:mutation"):
            if not any(
                item.subject and item.subject.subject_id == old_subject_id
                for item in self.subjects()
            ):
                raise FileNotFoundError(old_subject_id)
            adapter = self._adapter()
            with adapter.locked() as payload:
                records = []
                for record in payload["signals"]:
                    signal = LearningSignal.model_validate(record)
                    refs = [
                        replacement if ref.subject_id == old_subject_id else ref
                        for ref in signal.subject_refs
                    ]
                    if refs != signal.subject_refs:
                        signal = signal.model_copy(update={"subject_refs": refs})
                    records.append(signal.model_dump(mode="json", exclude_none=True, by_alias=True))
                payload["signals"] = records
                adapter.replace_all(payload)
            self._rebuild_profiles_locked()
            return self.subject_profile(replacement.subject_id)

    def _personality_prior(self) -> dict[str, Any]:
        profiles = list_trait_profiles()
        if not profiles:
            return {}
        latest = max(profiles, key=lambda value: str(value.get("created_at") or ""))
        return build_initial_slr_support(latest.get("scores") or {})

    @staticmethod
    def _bounded_text(value: str, limit: int = 180) -> str:
        return " ".join(value.split())[:limit]

    def _curate_memory_snapshot(self, profile: LearnerProfile) -> LearnerMemorySnapshot:
        """Create the Hermes-style bounded durable frame from explicit evidence.

        Inferred preferences and raw chat text deliberately never enter this
        frame.  The snapshot is compact enough for a stable prompt prefix and
        always carries the evidence references that justify it.
        """
        explicit = [item for item in profile.preferences if item.state == "explicit"]
        goals = [self._bounded_text(item.value) for item in explicit if item.category == "goal"][:2]
        preferences = [
            self._bounded_text(item.value) for item in explicit if item.category != "goal"
        ][:6]
        constraints = [
            self._bounded_text(item.value)
            for item in profile.preferences
            if item.state == "rejected"
        ][:4]
        refs = list(dict.fromkeys(ref for item in explicit for ref in item.evidence_refs))[:24]
        canonical = json.dumps(
            {"goals": goals, "preferences": preferences, "constraints": constraints, "refs": refs},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return LearnerMemorySnapshot(
            snapshot_id=f"learner-{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:20]}",
            created_at=_now(),
            goals=goals,
            explicit_preferences=preferences,
            constraints=constraints,
            evidence_refs=refs,
        )

    def _session_memory_snapshot(
        self, session_id: str, profile: LearnerProfile
    ) -> LearnerMemorySnapshot:
        """Freeze durable memory at session start; BKT remains intentionally live."""
        sessions = self._sessions()
        state = dict(sessions.get(session_id) or {})
        raw = state.get("memory_snapshot")
        if isinstance(raw, Mapping):
            try:
                return LearnerMemorySnapshot.model_validate(raw)
            except ValueError:
                pass
        snapshot = self._curate_memory_snapshot(profile)
        state["memory_snapshot"] = snapshot.model_dump()
        state.setdefault("created_at", _now())
        sessions[session_id] = state
        self._write_sessions(sessions)
        return snapshot

    def _remember_session(self, session_id: str, context: PersonalizationContext) -> None:
        sessions = self._sessions()
        sessions[session_id] = {
            "purpose": context.purpose,
            "subject_id": context.subject.subject_id if context.subject else None,
            "subject": context.subject.model_dump() if context.subject else None,
            "trace_id": context.trace_id,
            "updated_at": _now(),
            "memory_snapshot": context.memory_snapshot.model_dump()
            if context.memory_snapshot
            else None,
        }
        self._write_sessions(sessions)

    def build_context(
        self,
        *,
        purpose: Literal["chat", "courseware", "flashcards", "quiz"],
        subject: SubjectRef | None = None,
        current_instruction: str = "",
        material_analysis: Mapping[str, Any] | None = None,
        title: str = "",
        text: str = "",
        session_id: str = "",
    ) -> PersonalizationContext:
        try:
            if subject is None and session_id:
                # A follow-up chat often has no new attachment. Reuse the
                # last session-scoped, user-owned material subject before
                # falling back to a lightweight text rule.
                sessions = self._sessions()
                if sessions:
                    remembered_state = dict(sessions.get(session_id) or {})
                    raw_subject = remembered_state.get("subject")
                    if isinstance(raw_subject, Mapping):
                        subject = SubjectRef.model_validate(raw_subject)
                    if subject is None:
                        remembered_id = str(remembered_state.get("subject_id") or "")
                        remembered = self.subject_profile(remembered_id) if remembered_id else None
                        subject = remembered.subject if remembered and remembered.subject else None
            subject = subject or self.classify_subject(
                material_analysis=material_analysis, title=title, text=text
            )
            global_profile = self.global_profile()
            snapshot = (
                self._session_memory_snapshot(session_id, global_profile)
                if session_id
                else self._curate_memory_snapshot(global_profile)
            )
            subject_profile = (
                self.subject_profile(subject.subject_id)
                if subject and subject.confidence >= 0.65
                else None
            )
            scoped_preferences = (
                subject_profile.preferences if subject_profile else []
            ) + global_profile.preferences
            explicit = [item for item in scoped_preferences if item.state == "explicit"]
            rejected = [item for item in scoped_preferences if item.state == "rejected"]
            strategy_evidence = (
                subject_profile.strategy_evidence
                if subject_profile and global_profile.inference_enabled
                else []
            )
            selected = max(
                strategy_evidence,
                key=lambda item: (item.positive_weight - item.negative_weight, item.confidence),
                default=None,
            )
            # A strategy inferred from behavior needs three independent events;
            # explicit rejections still take effect immediately as constraints.
            selected = (
                selected
                if selected
                and len(selected.event_ids) >= 3
                and selected.positive_weight > selected.negative_weight
                else None
            )
            plan = selected.strategy if selected else TeachingAction()
            rationale = []
            if current_instruction:
                rationale.append(
                    VisibleRationale(
                        source="current_instruction",
                        text="Applied your current request.",
                        evidence_refs=[],
                    )
                )
            for preference in explicit[:2]:
                rationale.append(
                    VisibleRationale(
                        source="explicit_preference",
                        text=f"Used your preference: {preference.value}",
                        evidence_refs=preference.evidence_refs,
                    )
                )
            if selected:
                rationale.append(
                    VisibleRationale(
                        source="strategy_evidence",
                        text="Used a strategy supported by your prior feedback in this subject.",
                        evidence_refs=selected.evidence_refs,
                    )
                )
            if not rationale and self._personality_prior():
                rationale.append(
                    VisibleRationale(
                        source="personality_prior",
                        text="Used a bounded teaching-support cue from your active profile.",
                        evidence_refs=[],
                    )
                )
            if not rationale:
                rationale.append(
                    VisibleRationale(
                        source="default",
                        text="Used TraitTutor's standard teaching structure.",
                        evidence_refs=[],
                    )
                )
            goals = [item.value for item in explicit if item.category == "goal"]
            from traittutor.learning_model.decay import project_concept_signal

            signals = (
                sorted(
                    (project_concept_signal(signal) for signal in subject_profile.concept_signals),
                    key=lambda item: (
                        item.support_level != "needs_support",
                        item.mastery_probability,
                        -item.verified_observation_count,
                    ),
                )[:5]
                if subject_profile
                else []
            )
            # Projection precedes sorting and truncation so a stale formerly
            # supported concept cannot crowd out a current support need.
            evidence_refs = list(
                dict.fromkeys(ref for item in rationale for ref in item.evidence_refs)
            )
            teaching_plan = TeachingStrategyPlan(
                **plan.model_dump(),
                srl_support=["goal", "monitor"],
                rationale=rationale,
                evidence_refs=evidence_refs,
            )
            context = PersonalizationContext(
                purpose=purpose,
                subject=subject,
                active_goal=goals[0] if goals else None,
                plan=teaching_plan,
                memory_snapshot=snapshot,
                relevant_concept_signals=signals,
                constraints=[item.value for item in rejected],
                evidence_refs=evidence_refs,
                trace_id=f"personalization:{uuid4().hex}",
            )
            if session_id:
                self._remember_session(session_id, context)
            return context
        except Exception:
            # Personalization must never block teaching, so every failure
            # degrades to the standard structure. But silently swallowing it
            # hides exactly the signals operators need — a PermissionError
            # here means an ACL regression, and a KeyError/ValidationError
            # means corrupted memory state. Log with purpose/session so the
            # degraded trace_ids in telemetry can be matched to a cause.
            logger.exception(
                "build_context degraded to standard teaching structure purpose=%s session_id=%s",
                purpose,
                session_id or "-",
            )
            return PersonalizationContext(
                purpose=purpose,
                plan=TeachingStrategyPlan(
                    rationale=[
                        VisibleRationale(
                            source="default", text="Used TraitTutor's standard teaching structure."
                        )
                    ]
                ),
                trace_id=f"personalization:{uuid4().hex}",
                degraded=True,
                degradation_reason="memory_unavailable",
            )


_service: PersonalizationService | None = None


def get_personalization_service() -> PersonalizationService:
    global _service
    if _service is None:
        _service = PersonalizationService()
    # A durable status record survives process restarts.  The next API/runtime
    # access reclaims unfinished work instead of treating queued memory
    # reconciliation as silently complete.
    status = _service.memory_reconcile_status()
    if status.get("state") in {"queued", "running"} and _service._owner() not in _reconcile_tasks:
        _service.enqueue_memory_reconcile()
    return _service
