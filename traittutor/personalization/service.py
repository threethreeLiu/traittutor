"""Per-user learner-model storage and deterministic teaching-plan selection."""
from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
import json
import re
from pathlib import Path
from typing import Any, Literal, Mapping
from uuid import uuid4

from traittutor.assessment.big_five import build_initial_slr_support, list_trait_profiles
from traittutor.multi_user.context import get_current_user
from traittutor.services.memory import paths as memory_paths

from .models import (ConceptSignal, LearnerEvent, LearnerProfile, LearningSignal,
                     LearnerMemorySnapshot, PersonalizationContext, PreferenceEvidence, StrategyEvidence,
                     ReflectionView, SubjectRef, SubjectUnderstanding, TeachingAction,
                     TeachingStrategyPlan, VisibleRationale)

_locks: dict[str, asyncio.Lock] = {}
_reconcile_tasks: dict[str, asyncio.Task[object]] = {}
_INFERENCE_TTL = timedelta(days=90)
_RULE_SUBJECTS = (("python", "Computer Science", "computer-science", "Python"), ("algorithm", "Computer Science", "computer-science", "Algorithms"), ("statistics", "Mathematics", "mathematics", "Statistics"), ("心理", "Psychology", "psychology", "Psychology"), ("物理", "Science", "science", "Physics"))


def _now() -> str: return datetime.now(UTC).isoformat()
def _slug(value: str) -> str: return re.sub(r"[^\w]+", "-", value.lower(), flags=re.UNICODE).strip("-") or "unclassified"
def _safe_subject_id(value: str) -> str:
    """Keep user-controlled subject ids inside the owner-scoped learner root."""
    subject_id = value.strip()
    if not re.fullmatch(r"[\w.-]{1,100}", subject_id, flags=re.UNICODE) or subject_id in {".", ".."}:
        raise ValueError("invalid subject id")
    return subject_id

def _lock(key: str) -> asyncio.Lock:
    return _locks.setdefault(key, asyncio.Lock())


def _event_policy(event: LearnerEvent) -> tuple[float, float, float, float, bool]:
    """Return BKT transition/guess/slip/weight and verified-observation flag."""
    if event.event_type in {"mastery_attempt", "quiz_answer"}:
        return .15, .20, .10, 1.0, True
    if event.event_type == "flashcard_review":
        return .08, .28, .16, .55, True
    if event.event_type == "self_assessment":
        return .02, .50, .50, .15, False
    # Completion and chat events can reveal engagement, not mastery.
    return 0.0, .50, .50, 0.0, False


def _bkt_update(prior: float, *, correct: bool, transition: float, guess: float,
                slip: float, weight: float) -> float:
    predicted = prior + (1 - prior) * transition
    likelihood = predicted * (1 - slip) + (1 - predicted) * guess if correct else predicted * slip + (1 - predicted) * (1 - guess)
    posterior = predicted if likelihood <= 0 else (predicted * (1 - slip if correct else slip)) / likelihood
    return max(0.0, min(1.0, prior * (1 - weight) + posterior * weight))


def _understanding(concepts: list[ConceptSignal]) -> SubjectUnderstanding | None:
    if not concepts:
        return None
    observed = [item for item in concepts if item.observation_count]
    verified = [item for item in concepts if item.verified_observation_count]
    mastery = sum(item.mastery_probability for item in verified) / len(verified) if verified else 0.0
    confidence = min(1.0, sum(item.verified_observation_count for item in concepts) / max(3, len(concepts) * 3))
    if not verified:
        status = "starting"
    elif mastery >= .78 and confidence >= .65:
        status = "verified"
    elif mastery >= .58:
        status = "familiar"
    else:
        status = "learning"
    recent = max((item.last_practised_at for item in concepts if item.last_practised_at), default=None)
    return SubjectUnderstanding(
        status=status, concept_count=len(concepts), observed_concept_count=len(observed),
        coverage=len(observed) / len(concepts), verified_mastery=mastery,
        confidence=confidence, recent_activity_at=recent,
        review_load=sum(1 for item in concepts if item.support_level == "needs_support"),
    )


class PersonalizationService:
    def _root(self) -> Path: return memory_paths.memory_root() / "learner"
    def _global_path(self) -> Path: return self._root() / "global.json"
    def _subject_path(self, subject_id: str) -> Path: return self._root() / "subjects" / f"{_safe_subject_id(subject_id)}.json"
    def _signals_path(self) -> Path: return self._root() / "signals" / f"{datetime.now(UTC).strftime('%Y-%m')}.jsonl"
    def _sessions_path(self) -> Path: return self._root() / "sessions.json"
    def _jobs_path(self) -> Path: return self._root() / "jobs.json"

    def _owner(self) -> str: return get_current_user().id
    def _default_profile(self, scope: Literal["global", "subject"], subject: SubjectRef | None = None) -> LearnerProfile:
        return LearnerProfile(owner_id=self._owner(), scope=scope, subject=subject, updated_at=_now())

    def _read_profile(self, path: Path, scope: Literal["global", "subject"], subject: SubjectRef | None = None) -> LearnerProfile:
        if not path.exists(): return self._default_profile(scope, subject)
        payload = json.loads(path.read_text(encoding="utf-8"))
        profile = LearnerProfile.model_validate(payload)
        if profile.owner_id != self._owner(): raise PermissionError("learner profile ownership mismatch")
        return profile

    def _write_profile(self, path: Path, profile: LearnerProfile) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(profile.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(path)

    def classify_subject(self, *, material_analysis: Mapping[str, Any] | None = None, title: str = "", text: str = "") -> SubjectRef | None:
        analysis = dict(material_analysis or {})
        raw_subject = str(analysis.get("subject") or analysis.get("big_subject") or "").strip()
        sub = str(analysis.get("sub_subject") or analysis.get("small_subject") or "").strip()
        confidence = float(analysis.get("confidence") or 0) if raw_subject else 0
        if raw_subject:
            return SubjectRef(subject_id=_slug(raw_subject), label=raw_subject, path=[raw_subject, *([sub] if sub else [])], confidence=min(1, confidence), source="material_analysis", confirmed=False)
        haystack = f"{title} {text[:3000]}".lower()
        for token, label, subject_id, topic in _RULE_SUBJECTS:
            if token in haystack:
                return SubjectRef(subject_id=subject_id, label=label, path=[label, topic], confidence=.66, source="rule", confirmed=False)
        return None

    async def record_event(self, event: LearnerEvent, *, trusted: bool = False) -> list[LearnerProfile]:
        """Persist one normalized event and synchronously refresh its read model.

        The event is represented by the existing append-only signal audit so
        legacy evidence controls and deterministic rebuilds remain one system.
        """
        # Only server-owned study flows may create observations that change
        # verified mastery.  A browser can still submit an explicit self-report
        # or correction, but it must never be able to masquerade as a graded
        # quiz/mastery result simply by choosing an event_type.
        if not trusted and event.event_type not in {"self_assessment", "chat_correction", "strategy_feedback"}:
            raise PermissionError("event type must be recorded by a trusted learning flow")
        payload = {
            **event.payload,
            "event_type": event.event_type,
            "concept": event.concept_label or event.concept_id or "",
            "concept_id": event.concept_id or "",
            "module_id": event.module_id or "",
            "observation": event.observation,
            "event_confidence": event.confidence,
        }
        if event.event_type in {"mastery_attempt", "quiz_answer", "flashcard_review", "self_assessment"}:
            payload["correct"] = event.observation in {"correct", "known"}
        kind: Literal["explicit_preference", "learner_event"] = "explicit_preference" if event.event_type == "memory_preference" else "learner_event"
        if kind == "explicit_preference":
            payload["value"] = str(event.payload.get("value") or event.payload.get("text") or "").strip()
            payload["category"] = str(event.payload.get("category") or "explanation")
        signal = LearningSignal(
            signal_id=event.event_id, kind=kind, subject_refs=[event.subject] if event.subject else [],
            payload=payload, evidence_refs=event.evidence_refs, source="user" if event.event_type in {"self_assessment", "memory_preference"} else "system",
            occurred_at=event.occurred_at,
        )
        return await self.apply_signal(signal)

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
        path = self._jobs_path()
        if not path.exists():
            return {"state": "idle", "last_completed_at": None, "error": None}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {"state": "idle"}
        except (OSError, json.JSONDecodeError):
            return {"state": "idle", "last_completed_at": None, "error": "state_unreadable"}

    def _write_reconcile_status(self, value: Mapping[str, Any]) -> None:
        path = self._jobs_path(); path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(dict(value), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    async def reconcile_memory(self) -> dict[str, Any]:
        """Extract only explicit L3 preference entries, retaining document refs."""
        from traittutor.services.memory.document import parse

        owner = self._owner()
        async with _lock(f"{owner}:memory-reconcile"):
            self._write_reconcile_status({"state": "running", "started_at": _now(), "error": None})
            imported = 0
            try:
                path = memory_paths.l3_file("preferences")
                entries = []
                if path.exists():
                    entries = parse(path.read_text(encoding="utf-8")).all_entries()
                # L3 entry IDs survive edits.  Bind a derived event to the
                # content+refs hash, so an edit replaces—not silently retains—
                # the old preference.
                def entry_event_id(entry: Any) -> str:
                    content = str(entry.text) + "\x1f" + "\x1e".join(entry.refs)
                    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
                    return f"memory-{entry.id}-{digest}"

                valid_event_ids = {entry_event_id(entry) for entry in entries}
                # A manual memory edit/delete invalidates the derived event;
                # remove it first so rebuild never silently preserves stale text.
                for signal in self._all_signals():
                    if signal.signal_id.startswith("memory-") and signal.signal_id not in valid_event_ids:
                        await self.delete_evidence(signal.signal_id)
                for entry in entries:
                    text = entry.text.strip()
                    if not text:
                        continue
                    event = LearnerEvent(
                        event_id=entry_event_id(entry), event_type="memory_preference",
                        confidence=1.0, evidence_refs=[entry.id, *entry.refs],
                        payload={"value": text, "category": "explanation"}, occurred_at=_now(),
                    )
                    before = len(self.evidence())
                    await self.record_event(event, trusted=True)
                    imported += int(len(self.evidence()) > before)
                result = {"state": "completed", "last_completed_at": _now(), "imported": imported, "error": None}
                self._write_reconcile_status(result)
                return result
            except Exception as exc:
                result = {"state": "failed", "last_completed_at": None, "imported": imported, "error": type(exc).__name__}
                self._write_reconcile_status(result)
                return result

    def enqueue_memory_reconcile(self) -> dict[str, Any]:
        status = self.memory_reconcile_status()
        if status.get("state") == "running" and self._owner() in _reconcile_tasks:
            return status
        attempts = int(status.get("attempts") or 0)
        self._write_reconcile_status({"state": "queued", "queued_at": _now(), "error": None, "attempts": attempts})
        try:
            owner = self._owner()
            task = asyncio.get_running_loop().create_task(self.reconcile_memory())
            _reconcile_tasks[owner] = task
            def _finished(done: asyncio.Task[object]) -> None:
                _reconcile_tasks.pop(owner, None)
                if done.cancelled() or done.exception() is not None:
                    self._write_reconcile_status({"state": "queued", "queued_at": _now(), "error": "worker_interrupted", "attempts": attempts + 1})
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
        profiles: list[LearnerProfile] = []
        signal_path = self._signals_path()
        # One owner-wide mutation lock makes append, delete/rebuild and profile
        # projection a single serialized transaction for the file store.
        async with _lock(f"{owner}:mutation"):
            signal_path.parent.mkdir(parents=True, exist_ok=True)
            # Idempotency is global to the learner audit, not just the current
            # month: queued retries can legitimately cross a month boundary.
            if any(existing.signal_id == signal.signal_id for existing in self._all_signals()):
                return profiles
            with signal_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(signal.model_dump(exclude_none=True, by_alias=True), ensure_ascii=False, separators=(",", ":")) + "\n")
        targets = signal.subject_refs or [None]
        for subject in targets:
            scope: Literal["global", "subject"] = "subject" if subject and subject.confidence >= .65 else "global"
            path = self._subject_path(subject.subject_id) if scope == "subject" and subject else self._global_path()
            async with _lock(f"{owner}:{scope}:{subject.subject_id if subject else 'global'}"):
                profile = self._read_profile(path, scope, subject)
                profile = self._apply_to_profile(profile, signal)
                self._write_profile(path, profile)
                profiles.append(profile)
        return profiles

    def _apply_to_profile(self, profile: LearnerProfile, signal: LearningSignal) -> LearnerProfile:
        data = profile.model_dump()
        prefs = [PreferenceEvidence.model_validate(item) for item in data["preferences"]]
        if signal.kind == "reflection_decision":
            target_id = str(signal.payload.get("reflection_id") or "")
            decision = str(signal.payload.get("decision") or "")
            if target_id and decision in {"candidate", "confirmed", "rejected"}:
                state_by_decision = {"candidate": "inferred", "confirmed": "explicit", "rejected": "rejected"}
                prefs = [
                    item.model_copy(update={
                        "state": state_by_decision[decision],
                        "confidence": 1.0 if decision == "confirmed" else item.confidence,
                        "updated_at": _now(),
                        "expires_at": None if decision == "confirmed" else ((datetime.now(UTC) + _INFERENCE_TTL).isoformat() if decision == "candidate" else item.expires_at),
                    }) if item.id == target_id else item
                    for item in prefs
                ]
        if signal.kind in {"explicit_preference", "goal", "strategy_feedback"}:
            category = "goal" if signal.kind == "goal" else str(signal.payload.get("category") or "explanation")
            value = str(signal.payload.get("value") or signal.payload.get("text") or "").strip()
            if value:
                state = "explicit" if signal.source == "user" and signal.kind != "strategy_feedback" else ("rejected" if signal.payload.get("rejected") else "inferred")
                prefs = [p for p in prefs if not (p.category == category and p.value == value)]
                prefs.append(PreferenceEvidence(id=signal.signal_id, value=value, category=category, state=state, confidence=1.0 if state == "explicit" else .8, evidence_refs=signal.evidence_refs, updated_at=_now(), expires_at=None if state == "explicit" else (datetime.now(UTC)+_INFERENCE_TTL).isoformat()))
        concepts = [ConceptSignal.model_validate(item) for item in data["concept_signals"]]
        if signal.kind in {"quiz_attempt", "misconception", "learner_event"}:
            concept = str(signal.payload.get("concept") or "").strip()
            concept_id = str(signal.payload.get("concept_id") or "").strip() or _slug(concept)
            if concept and concept_id:
                prior = next((item for item in concepts if item.concept_id == concept_id), None)
                event_type = str(signal.payload.get("event_type") or "quiz_answer")
                observation = signal.payload.get("observation")
                event = LearnerEvent(
                    event_id=signal.signal_id, event_type=event_type if event_type in {"mastery_attempt", "quiz_answer", "flashcard_review", "courseware_outcome", "chat_correction", "self_assessment", "memory_preference", "memory_candidate", "strategy_feedback"} else "quiz_answer",
                    concept_id=concept_id, concept_label=concept,
                    module_id=str(signal.payload.get("module_id") or "") or None,
                    observation=observation if observation in {"correct", "incorrect", "known", "unknown", "uncertain", "engaged"} else ("correct" if signal.payload.get("correct") else "incorrect"),
                    confidence=float(signal.payload.get("event_confidence") or .7), evidence_refs=signal.evidence_refs,
                    occurred_at=signal.occurred_at,
                )
                transition, guess, slip, weight, verified = _event_policy(event)
                correct = event.observation in {"correct", "known"}
                attempts = (prior.attempt_count if prior else 0) + 1
                observations = (prior.observation_count if prior else 0) + 1
                verified_count = (prior.verified_observation_count if prior else 0) + (1 if verified else 0)
                probability = _bkt_update(prior.mastery_probability if prior else .2, correct=correct, transition=transition, guess=guess, slip=slip, weight=weight)
                support = "supported" if verified_count >= 2 and probability >= .75 else ("needs_support" if verified and probability < .4 else "developing")
                replacement = ConceptSignal(
                    concept_id=concept_id, label=concept, support_level=support,
                    confidence=min(.95, .25 + verified_count*.16 + event.confidence*.15), attempt_count=attempts,
                    misconception_tags=[] if correct else [str(signal.payload.get("misconception") or "needs review")],
                    evidence_refs=list(dict.fromkeys((prior.evidence_refs if prior else []) + signal.evidence_refs)), last_practised_at=_now(),
                    module_id=event.module_id or (prior.module_id if prior else None), mastery_probability=probability,
                    initial_mastery_probability=prior.initial_mastery_probability if prior else .2,
                    transition_probability=transition, guess_probability=guess, slip_probability=slip,
                    observation_count=observations, verified_observation_count=verified_count,
                    last_observation_source=event.event_type,
                )
                concepts = [item for item in concepts if item.concept_id != replacement.concept_id] + [replacement]
        strategies = [StrategyEvidence.model_validate(item) for item in data["strategy_evidence"]]
        inference_enabled = profile.inference_enabled if profile.scope == "global" else self.global_profile().inference_enabled
        if signal.kind in {"strategy_feedback", "artifact_outcome"} and inference_enabled:
            raw = signal.payload.get("strategy")
            if isinstance(raw, Mapping):
                strategy = TeachingAction.model_validate(raw)
                prior = next((item for item in strategies if item.task_type == signal.payload.get("task_type", "chat") and item.strategy == strategy), None)
                positive = (prior.positive_weight if prior else 0) + (1 if signal.payload.get("positive") else 0)
                negative = (prior.negative_weight if prior else 0) + (1 if signal.payload.get("negative") else 0)
                evidence = list(dict.fromkeys((prior.evidence_refs if prior else []) + signal.evidence_refs))
                event_ids = list(dict.fromkeys((prior.event_ids if prior else []) + [signal.signal_id]))
                replacement = StrategyEvidence(id=prior.id if prior else signal.signal_id, strategy=strategy, task_type=signal.payload.get("task_type", "chat"), positive_weight=positive, negative_weight=negative, confidence=min(.95, len(event_ids)/3), evidence_refs=evidence, event_ids=event_ids, last_observed_at=_now())
                strategies = [item for item in strategies if item.id != replacement.id] + [replacement]
        return LearnerProfile(owner_id=profile.owner_id, scope=profile.scope, subject=profile.subject, inference_enabled=profile.inference_enabled, preferences=prefs, concept_signals=concepts, strategy_evidence=strategies, understanding=_understanding(concepts) if profile.scope == "subject" else None, evidence_refs=list(dict.fromkeys(profile.evidence_refs + signal.evidence_refs)), schema_version=2, updated_at=_now(), needs_rebuild=False)

    def set_inference(self, enabled: bool) -> LearnerProfile:
        path = self._global_path(); profile = self._read_profile(path, "global")
        updated = profile.model_copy(update={"inference_enabled": enabled, "updated_at": _now()})
        self._write_profile(path, updated); return updated

    def global_profile(self) -> LearnerProfile: return self._read_profile(self._global_path(), "global")
    def subject_profile(self, subject_id: str) -> LearnerProfile:
        path = self._subject_path(subject_id)
        if not path.exists():
            return self._read_profile(path, "subject", None)
        return self._read_profile(path, "subject")

    def reconcile_graph_concepts(self, subject: SubjectRef, nodes: list[Mapping[str, Any]]) -> None:
        """Replace early chunk-id BKT entries once a grounded graph is available.

        Generation and review can happen before the background graph build
        finishes.  In that window we retain the source chunk id rather than
        dropping a learning event; this deterministic reconciliation makes the
        later graph node the canonical BKT key without losing observations.
        """
        path = self._subject_path(subject.subject_id)
        if not path.exists():
            return
        candidates: dict[str, Mapping[str, Any]] = {}
        for node in nodes:
            for chunk_id in node.get("evidence_chunk_ids", []):
                current = candidates.get(str(chunk_id))
                if current is None or float(node.get("confidence") or 0) > float(current.get("confidence") or 0):
                    candidates[str(chunk_id)] = node
        if not candidates:
            return
        profile = self._read_profile(path, "subject")
        updated: dict[str, ConceptSignal] = {}
        changed = False
        for signal in profile.concept_signals:
            node = candidates.get(signal.concept_id)
            if node is None:
                updated[signal.concept_id] = signal
                continue
            concept_id = str(node.get("concept_id") or signal.concept_id)
            replacement = signal.model_copy(update={
                "concept_id": concept_id,
                "label": str(node.get("label") or signal.label),
                "module_id": str(node.get("module_id") or signal.module_id or "") or None,
            })
            prior = updated.get(concept_id)
            if prior is not None:
                replacement = replacement.model_copy(update={
                    "attempt_count": prior.attempt_count + replacement.attempt_count,
                    "observation_count": prior.observation_count + replacement.observation_count,
                    "verified_observation_count": prior.verified_observation_count + replacement.verified_observation_count,
                    "mastery_probability": max(prior.mastery_probability, replacement.mastery_probability),
                    "confidence": max(prior.confidence, replacement.confidence),
                    "evidence_refs": list(dict.fromkeys(prior.evidence_refs + replacement.evidence_refs)),
                })
            updated[concept_id] = replacement
            changed = changed or concept_id != signal.concept_id
        if changed:
            concepts = list(updated.values())
            self._write_profile(path, profile.model_copy(update={
                "concept_signals": concepts,
                "understanding": _understanding(concepts),
                "updated_at": _now(),
            }))
    def subjects(self) -> list[LearnerProfile]:
        directory = self._root() / "subjects"
        if not directory.exists(): return []
        out=[]
        for path in directory.glob("*.json"):
            try: out.append(self._read_profile(path, "subject"))
            except (ValueError, OSError, PermissionError): continue
        return sorted(out, key=lambda item: item.updated_at, reverse=True)

    def overview(self) -> dict[str, Any]:
        global_profile = self.global_profile(); subjects = self.subjects()
        return {
            "global": global_profile.model_dump(), "subjects": [item.model_dump() for item in subjects],
            "inference_enabled": global_profile.inference_enabled,
            "pending_subjects": [item.model_dump() for item in subjects if item.subject and not item.subject.confirmed],
            "memory_reconcile": self.memory_reconcile_status(),
            "reflection_summary": self.reflection_summary(),
        }

    def reflections(self, *, subject_id: str | None = None) -> list[ReflectionView]:
        profiles = [self.global_profile(), *self.subjects()]
        if subject_id:
            profiles = [profile for profile in profiles if profile.subject and profile.subject.subject_id == subject_id]
        out: list[ReflectionView] = []
        now = datetime.now(UTC)
        for profile in profiles:
            for preference in profile.preferences:
                expired = bool(preference.expires_at and datetime.fromisoformat(preference.expires_at) <= now)
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
                out.append(ReflectionView(
                    reflection_id=preference.id, scope=profile.scope, subject=profile.subject,
                    category=preference.category, value=preference.value, status=status,
                    source_state=preference.state, confidence=preference.confidence,
                    evidence_refs=preference.evidence_refs, updated_at=preference.updated_at,
                    expires_at=preference.expires_at,
                    applies_to_compass=status == "confirmed",
                    reason="已确认，会用于下一次生成。" if status == "confirmed" else ("已拒绝，仅作为约束或审计记录。" if status == "rejected" else "候选记忆；确认前不会进入生成上下文。"),
                ))
            for concept in profile.concept_signals:
                confirmed = concept.verified_observation_count > 0
                status = "needs_rebuild" if profile.needs_rebuild else ("confirmed" if confirmed else "candidate")
                out.append(ReflectionView(
                    reflection_id=f"concept:{concept.concept_id}", scope=profile.scope, subject=profile.subject,
                    category="concept", value=f"{concept.label} · {concept.support_level}",
                    status=status, source_state=None, confidence=concept.confidence,
                    evidence_refs=concept.evidence_refs, updated_at=concept.last_practised_at or profile.updated_at,
                    applies_to_compass=confirmed and concept.support_level == "needs_support",
                    reason="来自可判分练习/复习，会用于安排薄弱概念。" if confirmed else "来自材料候选图谱，等待作答或复习证据确认。",
                ))
        return sorted(out, key=lambda item: item.updated_at, reverse=True)[:120]

    def reflection_summary(self) -> dict[str, int]:
        summary = {"confirmed": 0, "candidate": 0, "rejected": 0, "stale": 0, "needs_rebuild": 0, "applies_to_compass": 0}
        for reflection in self.reflections():
            summary[reflection.status] += 1
            summary["applies_to_compass"] += int(reflection.applies_to_compass)
        return summary

    async def decide_reflection(self, reflection_id: str, decision: Literal["candidate", "confirmed", "rejected"]) -> ReflectionView | None:
        existing = next((item for item in self.reflections() if item.reflection_id == reflection_id and item.category != "concept"), None)
        if existing is None:
            return None
        signal = LearningSignal(
            signal_id=f"reflection-{uuid4().hex}", kind="reflection_decision",
            subject_refs=[existing.subject] if existing.subject else [],
            payload={"reflection_id": reflection_id, "decision": decision},
            evidence_refs=list(dict.fromkeys([reflection_id, *existing.evidence_refs]))[:24],
            source="user", occurred_at=_now(),
        )
        await self.apply_signal(signal)
        return next((item for item in self.reflections() if item.reflection_id == reflection_id), None)

    def clear_session_state(self, session_id: str) -> bool:
        """Delete only the current user's auxiliary learner session state."""
        path = self._sessions_path()
        if not path.exists():
            return False
        sessions = json.loads(path.read_text(encoding="utf-8"))
        if session_id not in sessions:
            return False
        sessions.pop(session_id, None)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(sessions, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        return True

    def _all_signals(self) -> list[LearningSignal]:
        signals: list[LearningSignal] = []
        directory = self._root() / "signals"
        if not directory.exists():
            return signals
        for path in sorted(directory.glob("*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    signal = LearningSignal.model_validate_json(line)
                    if signal.owner_id in {None, self._owner()}:
                        signals.append(signal)
                except ValueError:
                    continue
        return signals

    def evidence(self, *, subject_id: str | None = None) -> list[LearningSignal]:
        signals = self._all_signals()
        if subject_id:
            signals = [signal for signal in signals if any(ref.subject_id == subject_id for ref in signal.subject_refs)]
        return sorted(signals, key=lambda item: item.occurred_at, reverse=True)[:100]

    async def delete_evidence(self, signal_id: str) -> bool:
        """Remove an audit event and deterministically rebuild affected views."""
        async with _lock(f"{self._owner()}:mutation"):
            return self._delete_evidence_locked(signal_id)

    def _delete_evidence_locked(self, signal_id: str) -> bool:
        """Delete/rebuild while the caller owns the owner mutation lock."""
        found = False
        directory = self._root() / "signals"
        for path in directory.glob("*.jsonl") if directory.exists() else []:
            retained = []
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    signal = LearningSignal.model_validate_json(line)
                except ValueError:
                    continue
                if signal.signal_id == signal_id:
                    found = True
                else:
                    retained.append(line)
            if found:
                tmp = path.with_suffix(".tmp")
                tmp.write_text("\n".join(retained) + ("\n" if retained else ""), encoding="utf-8")
                tmp.replace(path)
        if not found:
            return False
        self._rebuild_profiles_locked()
        return True

    def _rebuild_profiles_locked(self) -> None:
        """Reproject every profile from the remaining owner-scoped audit."""
        global_inference = self.global_profile().inference_enabled
        subject_dir = self._root() / "subjects"
        profile_paths = [self._global_path(), *(subject_dir.glob("*.json") if subject_dir.exists() else [])]
        for path in profile_paths:
            if path.exists():
                path.unlink()
        for signal in self._all_signals():
            # Rebuild without recursively appending audit lines.
            for subject in signal.subject_refs or [None]:
                scope = "subject" if subject and subject.confidence >= .65 else "global"
                path = self._subject_path(subject.subject_id) if scope == "subject" and subject else self._global_path()
                profile = self._read_profile(path, scope, subject)
                self._write_profile(path, self._apply_to_profile(profile, signal))
        profile = self.global_profile().model_copy(update={"inference_enabled": global_inference, "updated_at": _now()})
        self._write_profile(self._global_path(), profile)

    async def correct_subject(self, old_subject_id: str, replacement: SubjectRef) -> LearnerProfile:
        """Reassign auditable evidence instead of creating a second subject silo."""
        owner = self._owner()
        async with _lock(f"{owner}:mutation"):
            old_path = self._subject_path(old_subject_id)
            if not old_path.exists():
                raise FileNotFoundError(old_subject_id)
            directory = self._root() / "signals"
            for path in directory.glob("*.jsonl") if directory.exists() else []:
                changed = False; records: list[str] = []
                for line in path.read_text(encoding="utf-8").splitlines():
                    try:
                        signal = LearningSignal.model_validate_json(line)
                    except ValueError:
                        continue
                    refs = [replacement if ref.subject_id == old_subject_id else ref for ref in signal.subject_refs]
                    if refs != signal.subject_refs:
                        signal = signal.model_copy(update={"subject_refs": refs}); changed = True
                    records.append(json.dumps(signal.model_dump(exclude_none=True, by_alias=True), ensure_ascii=False, separators=(",", ":")))
                if changed:
                    tmp = path.with_suffix(".tmp"); tmp.write_text("\n".join(records) + ("\n" if records else ""), encoding="utf-8"); tmp.replace(path)
            self._rebuild_profiles_locked()
            return self.subject_profile(replacement.subject_id)

    def _personality_prior(self) -> dict[str, Any]:
        profiles = list_trait_profiles()
        if not profiles: return {}
        latest = max(profiles, key=lambda value: str(value.get("created_at") or ""))
        return build_initial_slr_support(latest.get("scores") or {})

    @staticmethod
    def _bounded_text(value: str, limit: int = 180) -> str:
        return " ".join(value.split())[:limit]

    def _curate_memory_snapshot(self, profile: LearnerProfile) -> LearnerMemorySnapshot:
        """Create the Hermes-style bounded durable frame from explicit evidence.

        Inferred preferences and raw L3/chat text deliberately never enter this
        frame.  The snapshot is compact enough for a stable prompt prefix and
        always carries the evidence references that justify it.
        """
        explicit = [item for item in profile.preferences if item.state == "explicit"]
        goals = [self._bounded_text(item.value) for item in explicit if item.category == "goal"][:2]
        preferences = [self._bounded_text(item.value) for item in explicit if item.category != "goal"][:6]
        constraints = [self._bounded_text(item.value) for item in profile.preferences if item.state == "rejected"][:4]
        refs = list(dict.fromkeys(ref for item in explicit for ref in item.evidence_refs))[:24]
        canonical = json.dumps(
            {"goals": goals, "preferences": preferences, "constraints": constraints, "refs": refs},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )
        return LearnerMemorySnapshot(
            snapshot_id=f"learner-{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:20]}",
            created_at=_now(), goals=goals, explicit_preferences=preferences,
            constraints=constraints, evidence_refs=refs,
        )

    def _session_memory_snapshot(self, session_id: str, profile: LearnerProfile) -> LearnerMemorySnapshot:
        """Freeze durable memory at session start; BKT remains intentionally live."""
        path = self._sessions_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        sessions = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
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
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(sessions, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        return snapshot

    def _remember_session(self, session_id: str, context: PersonalizationContext) -> None:
        path = self._sessions_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        sessions = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        sessions[session_id] = {
            "purpose": context.purpose, "subject_id": context.subject.subject_id if context.subject else None,
            "subject": context.subject.model_dump() if context.subject else None,
            "trace_id": context.trace_id, "updated_at": _now(),
            "memory_snapshot": context.memory_snapshot.model_dump() if context.memory_snapshot else None,
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(sessions, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    def build_context(self, *, purpose: Literal["chat", "courseware", "flashcards", "quiz"], subject: SubjectRef | None = None, current_instruction: str = "", material_analysis: Mapping[str, Any] | None = None, title: str = "", text: str = "", session_id: str = "") -> PersonalizationContext:
        try:
            if subject is None and session_id:
                # A follow-up chat often has no new attachment. Reuse the
                # last session-scoped, user-owned material subject before
                # falling back to a lightweight text rule.
                sessions_path = self._sessions_path()
                if sessions_path.exists():
                    sessions = json.loads(sessions_path.read_text(encoding="utf-8"))
                    remembered_state = dict(sessions.get(session_id) or {})
                    raw_subject = remembered_state.get("subject")
                    if isinstance(raw_subject, Mapping):
                        subject = SubjectRef.model_validate(raw_subject)
                    if subject is None:
                        remembered_id = str(remembered_state.get("subject_id") or "")
                        remembered = self.subject_profile(remembered_id) if remembered_id else None
                        subject = remembered.subject if remembered and remembered.subject else None
            subject = subject or self.classify_subject(material_analysis=material_analysis, title=title, text=text)
            global_profile = self.global_profile()
            snapshot = self._session_memory_snapshot(session_id, global_profile) if session_id else self._curate_memory_snapshot(global_profile)
            subject_profile = self.subject_profile(subject.subject_id) if subject and subject.confidence >= .65 else None
            scoped_preferences = (subject_profile.preferences if subject_profile else []) + global_profile.preferences
            explicit = [item for item in scoped_preferences if item.state == "explicit"]
            rejected = [item for item in scoped_preferences if item.state == "rejected"]
            strategy_evidence = subject_profile.strategy_evidence if subject_profile and global_profile.inference_enabled else []
            selected = max(strategy_evidence, key=lambda item: (item.positive_weight-item.negative_weight, item.confidence), default=None)
            # A strategy inferred from behavior needs three independent events;
            # explicit rejections still take effect immediately as constraints.
            selected = selected if selected and len(selected.event_ids) >= 3 and selected.positive_weight > selected.negative_weight else None
            plan = selected.strategy if selected else TeachingAction()
            rationale=[]
            if current_instruction: rationale.append(VisibleRationale(source="current_instruction", text="Applied your current request.", evidence_refs=[]))
            for preference in explicit[:2]: rationale.append(VisibleRationale(source="explicit_preference", text=f"Used your preference: {preference.value}", evidence_refs=preference.evidence_refs))
            if selected: rationale.append(VisibleRationale(source="strategy_evidence", text="Used a strategy supported by your prior feedback in this subject.", evidence_refs=selected.evidence_refs))
            if not rationale and self._personality_prior(): rationale.append(VisibleRationale(source="personality_prior", text="Used a bounded teaching-support cue from your active profile.", evidence_refs=[]))
            if not rationale: rationale.append(VisibleRationale(source="default", text="Used TraitTutor's standard teaching structure.", evidence_refs=[]))
            goals = [item.value for item in explicit if item.category == "goal"]
            signals = sorted(subject_profile.concept_signals, key=lambda item: (item.support_level != "needs_support", item.mastery_probability, -item.verified_observation_count))[:5] if subject_profile else []
            evidence_refs = list(dict.fromkeys(ref for item in rationale for ref in item.evidence_refs))
            teaching_plan = TeachingStrategyPlan(
                **plan.model_dump(), srl_support=["goal", "monitor"], rationale=rationale,
                evidence_refs=evidence_refs,
            )
            context = PersonalizationContext(
                purpose=purpose, subject=subject, active_goal=goals[0] if goals else None,
                plan=teaching_plan, memory_snapshot=snapshot, relevant_concept_signals=signals,
                constraints=[item.value for item in rejected],
                evidence_refs=evidence_refs, trace_id=f"personalization:{uuid4().hex}",
            )
            if session_id:
                self._remember_session(session_id, context)
            return context
        except Exception:
            return PersonalizationContext(purpose=purpose, plan=TeachingStrategyPlan(rationale=[VisibleRationale(source="default", text="Used TraitTutor's standard teaching structure.")]), trace_id=f"personalization:{uuid4().hex}", degraded=True, degradation_reason="memory_unavailable")

_service: PersonalizationService | None = None
def get_personalization_service() -> PersonalizationService:
    global _service
    if _service is None: _service = PersonalizationService()
    # A durable status record survives process restarts.  The next API/runtime
    # access reclaims unfinished work instead of treating queued memory
    # reconciliation as silently complete.
    status = _service.memory_reconcile_status()
    if status.get("state") in {"queued", "running"} and _service._owner() not in _reconcile_tasks:
        _service.enqueue_memory_reconcile()
    return _service
