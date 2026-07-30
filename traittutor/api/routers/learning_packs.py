"""Learning-pack APIs shared by courseware, card, and quiz pages."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from traittutor import learning_packs
from traittutor.generate.tasks import get_generation_task_manager
from traittutor.learning.grading import grade_answer
from traittutor.personalization.models import LearnerEvent
from traittutor.personalization.service import get_personalization_service
from traittutor.personalization.knowledge_graph import resolve_graph_concept

router = APIRouter()


class CreatePackRequest(BaseModel):
    title: str = ""
    material: dict[str, Any] = Field(default_factory=dict)
    profile_id: str | None = None


class UpdatePackRequest(BaseModel):
    title: str | None = None
    persona: str | None = None
    profile_id: str | None = None
    artifact: dict[str, Any] | None = None
    generation_id: str | None = Field(default=None, min_length=1, max_length=128)
    flashcard_progress: dict[str, Any] | None = None
    review_id: str | None = Field(default=None, min_length=1, max_length=128)
    quiz_attempt: dict[str, Any] | None = None


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
                observation="known" if value in {"mastered", "known"} else "unknown",
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
        submitted = str(attempt.get("submitted_at") or pack.get("updated_at") or "")
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            answer = str(answers.get(str(index), answers.get(index, ""))).strip()
            expected = str(item.get("correct_answer") or "").strip()
            correct = bool(answer and expected and grade_answer(answer, expected, str(item.get("question_type") or "short")))
            concept_id, concept_label, module_id = concept_for(item.get("node_id"), str(item.get("node_name") or item.get("question") or f"Question {index + 1}"))
            await service.record_event(LearnerEvent(
                event_id=f"pack-{pack_id}-quiz-{item.get('question_id', index)}-{submitted}", event_type="quiz_answer", subject=subject,
                concept_id=concept_id or f"quiz-{index}", concept_label=concept_label, module_id=module_id,
                observation="correct" if correct else "incorrect", confidence=.9,
                evidence_refs=[f"learning-pack:{pack_id}", f"question:{item.get('question_id', index)}"],
                payload={"answer_present": bool(answer)}, occurred_at=submitted,
            ), trusted=True)


@router.get("")
async def list_learning_packs():
    packs = learning_packs.list_packs()
    return {"packs": packs, "total": len(packs)}


@router.post("")
async def create_learning_pack(request: CreatePackRequest):
    return learning_packs.create_pack(title=request.title, material=request.material, profile_id=request.profile_id)


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
