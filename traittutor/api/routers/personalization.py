"""Authenticated learner-model API for the Profile application."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from traittutor.learning_model.decay import project_concept_signal
from traittutor.learning_model.knowledge_state import MIN_OBSERVATIONS_FOR_PROBABILITY
from traittutor.learning_model.stage_policy import EVIDENCE_STAGE_POLICY_VERSION
from traittutor.personalization.knowledge_graph import load_learning_knowledge_graph
from traittutor.personalization.models import (
    ConceptSignal,
    LearnerEvent,
    LearnerProfile,
    LearningSignal,
    SubjectRef,
)
from traittutor.personalization.service import get_personalization_service

router = APIRouter()


_PRIVATE_MASTERY_FIELDS = frozenset(
    {
        "mastery_probability",
        "initial_mastery_probability",
        "transition_probability",
        "guess_probability",
        "slip_probability",
        "mastery_interval",
        "verified_mastery",
        "bkt_calibrated",
        "bkt_param_version",
        "canonical_mastery_probability",
        "canonical_initial_mastery_probability",
    }
)


def _strip_private_mastery(value: Any) -> Any:
    """Defense in depth for non-profile legacy evidence projections."""
    if isinstance(value, dict):
        return {
            key: _strip_private_mastery(item)
            for key, item in value.items()
            if key not in _PRIVATE_MASTERY_FIELDS
        }
    if isinstance(value, list):
        return [_strip_private_mastery(item) for item in value]
    return value


def _public_concept_signal(signal: ConceptSignal, *, now: datetime | None = None) -> dict[str, Any]:
    """Allowlist one decayed, qualitative concept projection for the browser."""
    projected = project_concept_signal(signal, now=now)
    evidence_state = (
        projected.support_level
        if projected.bkt_calibrated
        and projected.verified_observation_count >= MIN_OBSERVATIONS_FOR_PROBABILITY
        else "insufficient_evidence"
    )
    return {
        "concept_id": projected.concept_id,
        "label": projected.label,
        "evidence_state": evidence_state,
        "change_signal": "none",
        "confidence": projected.confidence,
        "attempt_count": projected.attempt_count,
        "misconception_tags": list(projected.misconception_tags),
        "evidence_refs": list(projected.evidence_refs),
        "last_practised_at": projected.last_practised_at,
        "module_id": projected.module_id,
        "observation_count": projected.observation_count,
        "verified_observation_count": projected.verified_observation_count,
        "last_observation_source": projected.last_observation_source,
        "model_version": projected.bkt_param_version,
        "stage_policy_version": EVIDENCE_STAGE_POLICY_VERSION,
    }


def _public_profile(
    profile: LearnerProfile,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build a browser DTO by allowlist; private mastery is never serialized."""
    payload = profile.model_dump(
        mode="json",
        exclude={"owner_id", "concept_signals", "understanding"},
    )
    concepts = [_public_concept_signal(item, now=now) for item in profile.concept_signals]
    payload["concept_signals"] = concepts
    if profile.understanding is not None:
        understanding = profile.understanding.model_dump(
            mode="json",
            exclude={"verified_mastery", "mastery_interval"},
        )
        understanding["review_load"] = sum(
            item["evidence_state"] == "needs_support" for item in concepts
        )
        if understanding["status"] in {"familiar", "verified"} and any(
            item["evidence_state"] != "supported" for item in concepts
        ):
            understanding["status"] = "learning"
        payload["understanding"] = understanding
    else:
        payload["understanding"] = None
    return payload


class PreferenceRequest(BaseModel):
    value: str = Field(min_length=1, max_length=240)
    category: Literal["goal", "explanation", "pacing", "feedback", "constraint"] = "explanation"
    subject: SubjectRef | None = None


class SubjectConfirmRequest(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    path: list[str] = Field(default_factory=list, max_length=6)


class SubjectCorrectionRequest(SubjectConfirmRequest):
    subject_id: str = Field(min_length=1, max_length=100)


class FeedbackRequest(BaseModel):
    subject: SubjectRef | None = None
    task_type: Literal["chat", "courseware", "flashcards", "quiz"]
    strategy: dict[str, Any]
    positive: bool = False
    negative: bool = False
    rejected: bool = False
    evidence_refs: list[str] = Field(default_factory=list, max_length=24)


class ContextPreviewRequest(BaseModel):
    purpose: Literal["chat", "courseware", "flashcards", "quiz"]
    subject: SubjectRef | None = None
    title: str = ""
    text: str = Field(default="", max_length=24000)
    material_analysis: dict[str, Any] = Field(default_factory=dict)
    current_instruction: str = Field(default="", max_length=2000)


class ClearRequest(BaseModel):
    confirmed: bool = False


class ReflectionDecisionRequest(BaseModel):
    status: Literal["candidate", "confirmed", "rejected"]


class LearnerEventRequest(BaseModel):
    """Only user-authored, low-weight events are accepted from a browser."""

    event_type: Literal["self_assessment", "chat_correction"]
    subject_id: str | None = Field(default=None, min_length=1, max_length=100)
    concept_id: str | None = Field(default=None, max_length=160)
    concept_label: str | None = Field(default=None, max_length=160)
    observation: Literal["known", "unknown", "uncertain"] | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


@router.get("/learner/overview")
async def learner_overview():
    overview = get_personalization_service().overview()
    overview["global"] = _public_profile(overview["global"])
    overview["subjects"] = [_public_profile(item) for item in overview.get("subjects", [])]
    overview["pending_subjects"] = [
        _public_profile(item) for item in overview.get("pending_subjects", [])
    ]
    return overview


@router.get("/learner/subjects")
async def learner_subjects():
    return {
        "subjects": [
            _public_profile(profile) for profile in get_personalization_service().subjects()
        ]
    }


@router.get("/learner/subjects/{subject_id}")
async def learner_subject(subject_id: str):
    try:
        profile = get_personalization_service().subject_profile(subject_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Learner subject not found") from exc
    if profile.subject is None:
        raise HTTPException(status_code=404, detail="Learner subject not found")
    return _public_profile(profile)


@router.get("/learner/subjects/{subject_id}/knowledge-graph")
async def learner_knowledge_graph(subject_id: str):
    try:
        profile = get_personalization_service().subject_profile(subject_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Learner subject not found") from exc
    if profile.subject is None:
        raise HTTPException(status_code=404, detail="Learner subject not found")
    graph = load_learning_knowledge_graph(subject_id)
    return (
        graph.model_dump()
        if graph
        else {"subject": profile.subject.model_dump(), "nodes": [], "edges": [], "source_refs": []}
    )


@router.post("/learner/events")
async def record_learner_event(request: LearnerEventRequest):
    service = get_personalization_service()
    subject = None
    if request.subject_id:
        profile = service.subject_profile(request.subject_id)
        if profile.subject is None:
            raise HTTPException(status_code=404, detail="Learner subject not found")
        subject = profile.subject
    event = LearnerEvent(
        event_id=f"user-event-{uuid4().hex}",
        event_type=request.event_type,
        subject=subject,
        concept_id=request.concept_id,
        concept_label=request.concept_label,
        observation=request.observation,
        confidence=0.35,
        evidence_refs=[],
        payload=request.payload,
        occurred_at=datetime.now(UTC).isoformat(),
    )
    try:
        profiles = await service.record_event(event)
    except PermissionError as exc:
        # Untrusted browser events can never update BKT/mastery (invariant #2);
        # reject the disallowed event type cleanly instead of a 500.
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "profiles": [_public_profile(profile) for profile in profiles],
        "event_id": event.event_id,
    }


@router.post("/learner/reconcile-memory")
async def reconcile_learner_memory():
    return get_personalization_service().enqueue_memory_reconcile()


@router.get("/learner/reconcile-memory")
async def learner_memory_reconcile_status():
    return get_personalization_service().memory_reconcile_status()


@router.patch("/learner/preferences/global")
async def set_global_preference(request: PreferenceRequest):
    signal = LearningSignal(
        signal_id=f"pref-{uuid4().hex}",
        kind="explicit_preference",
        payload={"value": request.value, "category": request.category},
        evidence_refs=[],
        source="user",
        occurred_at=datetime.now(UTC).isoformat(),
    )
    profiles = await get_personalization_service().apply_signal(signal)
    return {"profiles": [_public_profile(profile) for profile in profiles]}


@router.patch("/learner/preferences/subject")
async def set_subject_preference(request: PreferenceRequest):
    if request.subject is None or request.subject.confidence < 0.65:
        raise HTTPException(
            status_code=422, detail="A confirmed subject is required for subject preferences"
        )
    signal = LearningSignal(
        signal_id=f"pref-{uuid4().hex}",
        kind="explicit_preference",
        subject_refs=[
            request.subject.model_copy(
                update={"confirmed": True, "source": "user", "confidence": 1.0}
            )
        ],
        payload={"value": request.value, "category": request.category},
        evidence_refs=[],
        source="user",
        occurred_at=datetime.now(UTC).isoformat(),
    )
    profiles = await get_personalization_service().apply_signal(signal)
    return {"profiles": [_public_profile(profile) for profile in profiles]}


@router.post("/learner/subjects/{subject_id}/confirm")
async def confirm_subject(subject_id: str, request: SubjectConfirmRequest):
    subject = SubjectRef(
        subject_id=subject_id,
        label=request.label,
        path=request.path or [request.label],
        confidence=1,
        source="user",
        confirmed=True,
    )
    signal = LearningSignal(
        signal_id=f"subject-{uuid4().hex}",
        kind="subject_correction",
        subject_refs=[subject],
        payload={},
        evidence_refs=[],
        source="user",
        occurred_at=datetime.now(UTC).isoformat(),
    )
    profiles = await get_personalization_service().apply_signal(signal)
    return {
        "profile": _public_profile(profiles[0])
        if profiles
        else _public_profile(get_personalization_service().subject_profile(subject_id))
    }


@router.post("/learner/subjects/{subject_id}/correct")
async def correct_subject(subject_id: str, request: SubjectCorrectionRequest):
    replacement = SubjectRef(
        subject_id=request.subject_id,
        label=request.label,
        path=request.path or [request.label],
        confidence=1,
        source="user",
        confirmed=True,
    )
    try:
        profile = await get_personalization_service().correct_subject(subject_id, replacement)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Learner subject not found") from exc
    return {"profile": _public_profile(profile)}


@router.post("/learner/feedback")
async def strategy_feedback(request: FeedbackRequest):
    if not request.positive and not request.negative and not request.rejected:
        raise HTTPException(
            status_code=422, detail="Provide positive, negative, or rejected feedback"
        )
    signal = LearningSignal(
        signal_id=f"feedback-{uuid4().hex}",
        kind="strategy_feedback",
        subject_refs=[request.subject] if request.subject else [],
        payload={
            "strategy": request.strategy,
            "task_type": request.task_type,
            "positive": request.positive,
            "negative": request.negative,
            "rejected": request.rejected,
        },
        evidence_refs=request.evidence_refs,
        source="user",
        occurred_at=datetime.now(UTC).isoformat(),
    )
    profiles = await get_personalization_service().apply_signal(signal)
    return {"profiles": [_public_profile(profile) for profile in profiles]}


@router.post("/learner/context/preview")
async def preview_context(request: ContextPreviewRequest):
    context = get_personalization_service().build_context(
        purpose=request.purpose,
        subject=request.subject,
        title=request.title,
        text=request.text,
        material_analysis=request.material_analysis,
        current_instruction=request.current_instruction,
    )
    payload = context.model_dump(mode="json", exclude={"relevant_concept_signals"})
    payload["relevant_concept_signals"] = [
        _public_concept_signal(signal) for signal in context.relevant_concept_signals
    ]
    return payload


@router.get("/learner/evidence")
async def learner_evidence(subject_id: str | None = None):
    return {
        "evidence": [
            _strip_private_mastery(signal.model_dump())
            for signal in get_personalization_service().evidence(subject_id=subject_id)
        ]
    }


@router.get("/learner/reflections")
async def learner_reflections(subject_id: str | None = None):
    service = get_personalization_service()
    reflections = service.reflections(subject_id=subject_id)
    return {
        "reflections": [item.model_dump() for item in reflections],
        "summary": service.reflection_summary(),
    }


@router.patch("/learner/reflections/{reflection_id}")
async def decide_reflection(reflection_id: str, request: ReflectionDecisionRequest):
    reflection = await get_personalization_service().decide_reflection(
        reflection_id, request.status
    )
    if reflection is None:
        raise HTTPException(status_code=404, detail="Reflection was not found or cannot be changed")
    return {"reflection": reflection.model_dump()}


@router.patch("/learner/inference")
async def set_inference(enabled: bool):
    return _public_profile(get_personalization_service().set_inference(enabled))


@router.delete("/learner/evidence/{signal_id}")
async def delete_evidence(signal_id: str):
    if not await get_personalization_service().delete_evidence(signal_id):
        raise HTTPException(status_code=404, detail="Evidence was not found")
    return {"deleted": True, "signal_id": signal_id}


@router.delete("/learner/sessions/{session_id}")
async def clear_learner_session(session_id: str):
    return {"cleared": get_personalization_service().clear_session_state(session_id)}


@router.delete("/learner")
async def clear_learner_model(request: ClearRequest):
    if not request.confirmed:
        raise HTTPException(status_code=422, detail="Set confirmed=true to clear the learner model")
    service = get_personalization_service()
    root = service._root()
    if root.exists() and root.name == "learner":
        import shutil

        shutil.rmtree(root)
    return {"cleared": True}
