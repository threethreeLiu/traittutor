"""Learning-pack APIs shared by courseware, card, and quiz pages."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from starlette.responses import StreamingResponse

from traittutor import learning_packs
from traittutor.generate.tasks import get_generation_task_manager
from traittutor.learning_components import (
    LearningComponentPlan,
    build_learning_component_plan,
)
from traittutor.learning.grading import grade_answer
from traittutor.personalization.models import LearnerEvent, SubjectRef
from traittutor.personalization.service import get_personalization_service
from traittutor.personalization.knowledge_graph import resolve_graph_concept

router = APIRouter()


class CreatePackRequest(BaseModel):
    title: str = ""
    material: dict[str, Any] = Field(default_factory=dict)
    profile_id: str | None = None
    goal: dict[str, Any] | str | None = None
    sources: list[dict[str, Any]] = Field(default_factory=list)


class UpdatePackRequest(BaseModel):
    title: str | None = None
    material: dict[str, Any] | None = None
    persona: str | None = None
    profile_id: str | None = None
    artifact: dict[str, Any] | None = None
    generation_id: str | None = Field(default=None, min_length=1, max_length=128)
    flashcard_progress: dict[str, Any] | None = None
    review_id: str | None = Field(default=None, min_length=1, max_length=128)
    quiz_attempt: dict[str, Any] | None = None
    goal: dict[str, Any] | str | None = None
    sources: list[dict[str, Any]] | None = None
    source: dict[str, Any] | None = None


class CreateLearningPlanRequest(BaseModel):
    instruction: str = Field(default="", max_length=1200)
    preferred_modalities: list[Literal["text", "visual", "audio", "interactive"]] = Field(default_factory=list, max_length=4)
    accessibility: dict[str, Any] = Field(default_factory=dict)
    supersedes_plan_id: str | None = Field(default=None, max_length=128)


class ComponentInteractionRequest(BaseModel):
    event_id: str | None = Field(default=None, max_length=128)
    action: Literal["start", "complete", "skip", "retry", "degrade", "feedback"]
    observation: Literal["correct", "incorrect", "known", "uncertain", "unknown"] | None = None
    question_id: str | None = Field(default=None, max_length=160)
    answer: str | None = Field(default=None, max_length=4000)
    concept_id: str | None = Field(default=None, max_length=160)
    concept_label: str | None = Field(default=None, max_length=160)
    output_ref: str | None = Field(default=None, max_length=240)
    feedback: str | None = Field(default=None, max_length=600)
    occurred_at: str | None = Field(default=None, max_length=80)
    replan: bool = True


def _checked_quiz_indexes(values: Any) -> set[int] | None:
    if not isinstance(values, list):
        return None
    indexes: set[int] = set()
    for value in values:
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            indexes.add(value)
            continue
        text = str(value).strip()
        if text.isdigit():
            indexes.add(int(text))
    return indexes


async def _record_pack_learning_events(pack: dict[str, Any], patch: dict[str, Any]) -> None:
    """Bridge consumer study-tool actions into the auditable learner model.

    The pack remains the source for its artifact and raw answers; learner events
    only retain stable IDs and the minimum concept/answer outcome needed for
    personalized follow-up.
    """
    service = get_personalization_service()
    material = pack.get("material") if isinstance(pack.get("material"), dict) else {}
    metadata = material.get("metadata") if isinstance(material.get("metadata"), dict) else {}
    analysis = metadata.get("learner_analysis") if isinstance(metadata.get("learner_analysis"), dict) else {}
    subject = service.classify_subject(material_analysis=analysis, title=str(material.get("title") or pack.get("title") or ""), text=str(material.get("text") or ""))
    pack_id = str(pack.get("pack_id") or "")
    artifacts = pack.get("artifacts") if isinstance(pack.get("artifacts"), dict) else {}
    def concept_for(node_id: Any, fallback_label: str) -> tuple[str, str, str | None]:
        raw_id = str(node_id or "").strip()
        if subject is not None and raw_id:
            mapped = resolve_graph_concept(subject.subject_id, raw_id)
            if mapped is not None:
                return mapped
        return raw_id, fallback_label, None
    # Saving a generated artifact is not proof that it was studied. Completion
    # events are emitted only by explicit learning interactions.
    if isinstance(patch.get("flashcard_progress"), dict):
        cards = artifacts.get("flashcards") or []
        items = cards[-1].get("items", []) if cards and isinstance(cards[-1], dict) else []
        by_node = {str(item.get("node_id")): item for item in items if isinstance(item, dict)}
        review_id = str(patch.get("review_id") or "")
        for node_id, state in patch["flashcard_progress"].items() if review_id else ():
            item = by_node.get(str(node_id), {})
            value = str(state).lower()
            concept_id, concept_label, module_id = concept_for(node_id, str(item.get("node_name") or item.get("front") or node_id))
            await service.record_event(LearnerEvent(
                event_id=f"pack-{pack_id}-card-{node_id}-{review_id}", event_type="flashcard_review", subject=subject,
                concept_id=concept_id, concept_label=concept_label, module_id=module_id,
                observation="known" if value in {"mastered", "known"} else "uncertain" if value in {"uncertain", "fuzzy"} else "unknown",
                confidence=.65, evidence_refs=[f"learning-pack:{pack_id}"], payload={"state": value},
                occurred_at=str(pack.get("updated_at") or ""),
            ), trusted=True)
    attempt = patch.get("quiz_attempt")
    if isinstance(attempt, dict):
        quizzes = artifacts.get("quiz") or []
        items = quizzes[-1].get("items", []) if quizzes and isinstance(quizzes[-1], dict) else []
        if not quizzes or not isinstance(quizzes[-1], dict) or not quizzes[-1].get("verified_generation_id"):
            return
        answers = attempt.get("answers") if isinstance(attempt.get("answers"), dict) else {}
        checked_indexes = _checked_quiz_indexes(attempt.get("checked"))
        submitted = str(attempt.get("submitted_at") or pack.get("updated_at") or "")
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            answer = str(answers.get(str(index), answers.get(index, ""))).strip()
            if not answer or (checked_indexes is not None and index not in checked_indexes):
                continue
            expected = str(item.get("correct_answer") or "").strip()
            options = item.get("options") if isinstance(item.get("options"), list) else []
            selected = next(
                (
                    option for option in options
                    if isinstance(option, dict)
                    and answer in {
                        str(option.get("text") or "").strip(),
                        str(option.get("key") or option.get("id") or "").strip(),
                    }
                ),
                None,
            )
            correct = bool(selected.get("is_correct")) if isinstance(selected, dict) else bool(expected and grade_answer(answer, expected, str(item.get("question_type") or "short")))
            concept_id, concept_label, module_id = concept_for(item.get("node_id"), str(item.get("node_name") or item.get("question") or f"Question {index + 1}"))
            await service.record_event(LearnerEvent(
                event_id=f"pack-{pack_id}-quiz-{item.get('question_id', index)}-{submitted}", event_type="quiz_answer", subject=subject,
                concept_id=concept_id or f"quiz-{index}", concept_label=concept_label, module_id=module_id,
                observation="correct" if correct else "incorrect", confidence=.9,
                evidence_refs=[f"learning-pack:{pack_id}", f"question:{item.get('question_id', index)}"],
                payload={"answer_present": True, "graded_by": "option_key" if isinstance(selected, dict) else "answer_match"},
                occurred_at=submitted,
            ), trusted=True)


def _build_component_plan(
    pack: dict[str, Any],
    request: CreateLearningPlanRequest,
) -> LearningComponentPlan:
    return build_learning_component_plan(
        pack,
        instruction=request.instruction,
        preferred_modalities=list(request.preferred_modalities),
        supersedes_plan_id=request.supersedes_plan_id,
    )


async def _record_component_learning_event(
    pack: dict[str, Any],
    plan: dict[str, Any],
    component: dict[str, Any],
    request: ComponentInteractionRequest,
    event_id: str,
) -> bool:
    """Record only answerable observations as BKT-facing evidence."""
    if request.observation is None:
        return False
    component_type = str(component.get("component_type") or "")
    assessment = component_type in {"diagnostic_check", "guided_practice", "transfer_challenge"}
    retrieval = component_type in {"retrieval_card", "review_queue"}
    if assessment and request.observation not in {"correct", "incorrect"}:
        return False
    if retrieval and request.observation not in {"known", "uncertain", "unknown"}:
        return False
    if not assessment and not retrieval:
        return False
    raw_subject = plan.get("subject_ref")
    subject = SubjectRef.model_validate(raw_subject) if isinstance(raw_subject, dict) else None
    if subject is None:
        return False
    concept_id = request.concept_id or next(iter(component.get("concept_refs") or []), component["component_id"])
    concept_label = request.concept_label or concept_id
    event_type = "quiz_answer" if assessment else "flashcard_review"
    await get_personalization_service().record_event(
        LearnerEvent(
            event_id=event_id,
            event_type=event_type,
            subject=subject,
            concept_id=concept_id,
            concept_label=concept_label,
            observation=request.observation,
            confidence=.9 if assessment else .65,
            evidence_refs=[
                f"learning-pack:{pack['pack_id']}",
                f"learning-plan:{plan['plan_id']}",
                f"learning-component:{component['component_id']}",
            ],
            payload={"component_type": component_type},
            occurred_at=request.occurred_at or str(pack.get("updated_at") or ""),
        ),
        trusted=True,
    )
    return True


def _verified_assessment_observation(
    pack: dict[str, Any],
    request: ComponentInteractionRequest,
) -> Literal["correct", "incorrect"] | None:
    """Grade an assessment answer against a server-owned generated artifact."""
    if not request.question_id or request.answer is None or not request.output_ref:
        return None
    artifacts = pack.get("artifacts") if isinstance(pack.get("artifacts"), dict) else {}
    quizzes = artifacts.get("quiz") if isinstance(artifacts.get("quiz"), list) else []
    artifact = next(
        (
            item for item in reversed(quizzes)
            if isinstance(item, dict) and item.get("verified_generation_id") == request.output_ref
        ),
        None,
    )
    if artifact is None:
        return None
    item = next(
        (
            item for item in artifact.get("items", [])
            if isinstance(item, dict) and str(item.get("question_id") or "") == request.question_id
        ),
        None,
    )
    if item is None:
        return None
    answer = request.answer.strip()
    options = item.get("options") if isinstance(item.get("options"), list) else []
    selected = next(
        (
            option for option in options
            if isinstance(option, dict)
            and answer in {
                str(option.get("text") or "").strip(),
                str(option.get("key") or option.get("id") or "").strip(),
            }
        ),
        None,
    )
    if isinstance(selected, dict):
        return "correct" if bool(selected.get("is_correct")) else "incorrect"
    expected = str(item.get("correct_answer") or "").strip()
    if not expected:
        return None
    return "correct" if grade_answer(answer, expected, str(item.get("question_type") or "short")) else "incorrect"


@router.get("")
async def list_learning_packs():
    packs = learning_packs.list_packs()
    return {"packs": packs, "total": len(packs)}


@router.post("")
async def create_learning_pack(request: CreatePackRequest):
    return learning_packs.create_pack(
        title=request.title,
        material=request.material,
        profile_id=request.profile_id,
        goal=request.goal,
        sources=request.sources,
    )


@router.get("/{pack_id}")
async def get_learning_pack(pack_id: str):
    pack = learning_packs.get_pack(pack_id)
    if pack is None:
        raise HTTPException(status_code=404, detail="Learning pack not found")
    return pack


@router.patch("/{pack_id}")
async def update_learning_pack(pack_id: str, request: UpdatePackRequest):
    patch = request.model_dump(exclude_none=True)
    generation_id = patch.pop("generation_id", None)
    if generation_id:
        task = get_generation_task_manager().get(generation_id)
        if task is None or task.status != "completed" or task.result is None:
            raise HTTPException(status_code=422, detail="Completed generation task not found")
        verified_artifact = dict(task.result.result)
        verified_artifact["verified_generation_id"] = generation_id
        patch["artifact"] = verified_artifact
    elif "artifact" in patch:
        # Artifacts are server-owned generation outputs.  Do not allow a client
        # to insert its own answer key and then submit it as a graded quiz.
        raise HTTPException(status_code=422, detail="Use generation_id to attach a generated artifact")
    pack = learning_packs.update_pack(pack_id, patch)
    if pack is None:
        raise HTTPException(status_code=404, detail="Learning pack not found")
    try:
        await _record_pack_learning_events(pack, patch)
    except Exception:
        # Progress persistence must not be blocked by personalization failures.
        pass
    return pack


@router.post("/{pack_id}/plans")
async def create_learning_component_plan(pack_id: str, request: CreateLearningPlanRequest):
    pack = learning_packs.get_pack(pack_id)
    if pack is None:
        raise HTTPException(status_code=404, detail="Learning pack not found")
    if request.supersedes_plan_id and learning_packs.get_component_plan(pack_id, request.supersedes_plan_id) is None:
        raise HTTPException(status_code=404, detail="Previous learning plan not found")
    plan = _build_component_plan(pack, request)
    saved = learning_packs.create_component_plan(pack_id, plan.model_dump())
    if saved is None:
        raise HTTPException(status_code=409, detail="Learning plan could not be saved")
    return {**saved, "start_url": f"/space/learning/{pack_id}"}


@router.get("/{pack_id}/plans")
async def list_learning_component_plans(pack_id: str):
    if learning_packs.get_pack(pack_id) is None:
        raise HTTPException(status_code=404, detail="Learning pack not found")
    plans = learning_packs.list_component_plans(pack_id)
    return {"plans": plans, "total": len(plans)}


@router.get("/{pack_id}/plans/{plan_id}")
async def get_learning_component_plan(pack_id: str, plan_id: str):
    plan = learning_packs.get_component_plan(pack_id, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Learning plan not found")
    return plan


@router.get("/{pack_id}/plans/{plan_id}/events")
async def stream_learning_component_events(pack_id: str, plan_id: str):
    pack = learning_packs.get_pack(pack_id)
    plan = learning_packs.get_component_plan(pack_id, plan_id)
    if pack is None or plan is None:
        raise HTTPException(status_code=404, detail="Learning plan not found")

    async def stream():
        progress = (pack.get("component_progress") or {}).get(plan_id) or {}
        yield f"event: snapshot\ndata: {json.dumps({'plan': plan, 'progress': progress}, ensure_ascii=False)}\n\n"
        await asyncio.sleep(0)
        yield "event: ready\ndata: {}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.post("/{pack_id}/plans/{plan_id}/components/{component_id}/events")
async def record_learning_component_event(
    pack_id: str,
    plan_id: str,
    component_id: str,
    request: ComponentInteractionRequest,
):
    pack = learning_packs.get_pack(pack_id)
    plan = learning_packs.get_component_plan(pack_id, plan_id)
    if pack is None or plan is None:
        raise HTTPException(status_code=404, detail="Learning plan not found")
    component = next((item for item in plan.get("components", []) if item.get("component_id") == component_id), None)
    if component is None:
        raise HTTPException(status_code=404, detail="Learning component not found")
    effective_request = request
    if component.get("component_type") in {"diagnostic_check", "guided_practice", "transfer_challenge"}:
        if request.observation is not None or request.action in {"feedback", "complete"}:
            verified_observation = _verified_assessment_observation(pack, request)
            if verified_observation is None:
                raise HTTPException(status_code=422, detail="A verified assessment answer is required")
            effective_request = request.model_copy(update={"observation": verified_observation})
    event = request.model_dump(exclude_none=True)
    event["event_id"] = request.event_id or f"component-{uuid4().hex}"
    if effective_request is not request:
        event["observation"] = effective_request.observation
    try:
        recorded = learning_packs.record_component_event(pack_id, plan_id, component_id, event)
    except learning_packs.InvalidComponentTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if recorded is None:
        raise HTTPException(status_code=409, detail="Learning component event could not be saved")
    updated_pack, updated_component = recorded
    learner_state_updated = False
    try:
        learner_state_updated = await _record_component_learning_event(
            updated_pack, plan, updated_component, effective_request, str(event["event_id"]),
        )
    except Exception:
        learner_state_updated = False

    replanned = None
    if learner_state_updated and request.replan:
        refreshed = learning_packs.get_pack(pack_id) or updated_pack
        replanned_model = _build_component_plan(
            refreshed,
            CreateLearningPlanRequest(
                instruction=str((refreshed.get("goal") or {}).get("text") or ""),
                supersedes_plan_id=plan_id,
            ),
        )
        replanned = learning_packs.create_component_plan(pack_id, replanned_model.model_dump())
    return {
        "component": updated_component,
        "learner_state_updated": learner_state_updated,
        "replanned_plan": replanned,
    }
