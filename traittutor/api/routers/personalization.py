"""Authenticated learner-model API for the Profile application."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from traittutor.personalization.models import LearnerEvent, LearningSignal, SubjectRef
from traittutor.personalization.service import get_personalization_service
from traittutor.personalization.knowledge_graph import load_learning_knowledge_graph

router = APIRouter()


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
    title: str = ""
    text: str = Field(default="", max_length=24000)
    material_analysis: dict[str, Any] = Field(default_factory=dict)
    current_instruction: str = Field(default="", max_length=2000)


class ClearRequest(BaseModel):
    confirmed: bool = False


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
    return get_personalization_service().overview()


@router.get("/learner/subjects")
async def learner_subjects():
    return {"subjects": [profile.model_dump() for profile in get_personalization_service().subjects()]}


@router.get("/learner/subjects/{subject_id}")
async def learner_subject(subject_id: str):
    try:
        profile = get_personalization_service().subject_profile(subject_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Learner subject not found") from exc
    if profile.subject is None:
        raise HTTPException(status_code=404, detail="Learner subject not found")
    return profile.model_dump()


@router.get("/learner/subjects/{subject_id}/knowledge-graph")
async def learner_knowledge_graph(subject_id: str):
    try:
        profile = get_personalization_service().subject_profile(subject_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Learner subject not found") from exc
    if profile.subject is None:
        raise HTTPException(status_code=404, detail="Learner subject not found")
    graph = load_learning_knowledge_graph(subject_id)
    return graph.model_dump() if graph else {"subject": profile.subject.model_dump(), "nodes": [], "edges": [], "source_refs": []}


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
        event_id=f"user-event-{uuid4().hex}", event_type=request.event_type, subject=subject,
        concept_id=request.concept_id, concept_label=request.concept_label,
        observation=request.observation, confidence=.35, evidence_refs=[], payload=request.payload,
        occurred_at=datetime.now(UTC).isoformat(),
    )
    profiles = await service.record_event(event)
    return {"profiles": [profile.model_dump() for profile in profiles], "event_id": event.event_id}


@router.post("/learner/reconcile-memory")
async def reconcile_learner_memory():
    return get_personalization_service().enqueue_memory_reconcile()


@router.get("/learner/reconcile-memory")
async def learner_memory_reconcile_status():
    return get_personalization_service().memory_reconcile_status()


@router.patch("/learner/preferences/global")
async def set_global_preference(request: PreferenceRequest):
    signal = LearningSignal(signal_id=f"pref-{uuid4().hex}", kind="explicit_preference", payload={"value": request.value, "category": request.category}, evidence_refs=[], source="user", occurred_at=datetime.now(UTC).isoformat())
    profiles = await get_personalization_service().apply_signal(signal)
    return {"profiles": [profile.model_dump() for profile in profiles]}


@router.patch("/learner/preferences/subject")
async def set_subject_preference(request: PreferenceRequest):
    if request.subject is None or request.subject.confidence < .65:
        raise HTTPException(status_code=422, detail="A confirmed subject is required for subject preferences")
    signal = LearningSignal(signal_id=f"pref-{uuid4().hex}", kind="explicit_preference", subject_refs=[request.subject.model_copy(update={"confirmed": True, "source": "user", "confidence": 1.0})], payload={"value": request.value, "category": request.category}, evidence_refs=[], source="user", occurred_at=datetime.now(UTC).isoformat())
    profiles = await get_personalization_service().apply_signal(signal)
    return {"profiles": [profile.model_dump() for profile in profiles]}


@router.post("/learner/subjects/{subject_id}/confirm")
async def confirm_subject(subject_id: str, request: SubjectConfirmRequest):
    subject = SubjectRef(subject_id=subject_id, label=request.label, path=request.path or [request.label], confidence=1, source="user", confirmed=True)
    signal = LearningSignal(signal_id=f"subject-{uuid4().hex}", kind="subject_correction", subject_refs=[subject], payload={}, evidence_refs=[], source="user", occurred_at=datetime.now(UTC).isoformat())
    profiles = await get_personalization_service().apply_signal(signal)
    return {"profile": profiles[0].model_dump() if profiles else get_personalization_service().subject_profile(subject_id).model_dump()}


@router.post("/learner/subjects/{subject_id}/correct")
async def correct_subject(subject_id: str, request: SubjectCorrectionRequest):
    replacement = SubjectRef(subject_id=request.subject_id, label=request.label, path=request.path or [request.label], confidence=1, source="user", confirmed=True)
    try:
        profile = await get_personalization_service().correct_subject(subject_id, replacement)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Learner subject not found") from exc
    return {"profile": profile.model_dump()}


@router.post("/learner/feedback")
async def strategy_feedback(request: FeedbackRequest):
    if not request.positive and not request.negative and not request.rejected:
        raise HTTPException(status_code=422, detail="Provide positive, negative, or rejected feedback")
    signal = LearningSignal(signal_id=f"feedback-{uuid4().hex}", kind="strategy_feedback", subject_refs=[request.subject] if request.subject else [], payload={"strategy": request.strategy, "task_type": request.task_type, "positive": request.positive, "negative": request.negative, "rejected": request.rejected}, evidence_refs=request.evidence_refs, source="user", occurred_at=datetime.now(UTC).isoformat())
    profiles = await get_personalization_service().apply_signal(signal)
    return {"profiles": [profile.model_dump() for profile in profiles]}


@router.post("/learner/context/preview")
async def preview_context(request: ContextPreviewRequest):
    context = get_personalization_service().build_context(purpose=request.purpose, title=request.title, text=request.text, material_analysis=request.material_analysis, current_instruction=request.current_instruction)
    return context.model_dump()


@router.get("/learner/evidence")
async def learner_evidence(subject_id: str | None = None):
    return {"evidence": [signal.model_dump() for signal in get_personalization_service().evidence(subject_id=subject_id)]}


@router.patch("/learner/inference")
async def set_inference(enabled: bool):
    return get_personalization_service().set_inference(enabled).model_dump()


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
