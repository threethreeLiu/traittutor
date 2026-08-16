"""TraitTutor Generate Suite API."""

from __future__ import annotations

import json
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Header, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, model_validator

from traittutor.components.page_store import PageStore
from traittutor.generate.document_material import LearningDocumentError, prepare_learning_document
from traittutor.generate.image_material import (
    MAX_IMAGE_BASE64_CHARS,
    LearningImageError,
    LearningImageUnavailable,
    canonical_prepared_image_material,
    is_image_material_candidate,
    prepare_learning_image,
)
from traittutor.generate.material_analysis import (
    ANALYSIS_MAX_METADATA_CHARS,
    ANALYSIS_MAX_PAGE_SLICE_CHARS,
    ANALYSIS_MAX_PAGE_SLICES,
    ANALYSIS_MAX_TEXT_CHARS,
    MaterialAnalysisRateLimitError,
    analyze_material,
    load_material_analysis,
)
from traittutor.generate.runner import GenerationConfigurationError
from traittutor.generate.service import (
    GenerationRequest,
    GenerationResult,
    MaterialSource,
    list_generations,
    load_generation,
)
from traittutor.generate.tasks import get_generation_task_manager
from traittutor.learning.event_chain import CanonicalAnswerEventChain
from traittutor.learning.grading import classify_error, grade_answer
from traittutor.learning.intent import scan_untrusted_learning_payload
from traittutor.learning.service import (
    LearningService,
    project_canonical_event_to_existing_progress,
)
from traittutor.multi_user.context import get_current_user

router = APIRouter()


def _learner_safe_task_result_dict(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a preview without answer keys or internal prompt paths.

    Generation tasks are learner-facing, so quiz answer keys must stay in the
    server-owned task/Pack artifact even after review confirmation. The
    ``prompt_asset`` field exposes internal prompt file paths (e.g.
    ``courseware/traittutor-courseware.md``) that the frontend never consumes —
    strip it from every response so only the opaque ``prompt_signature`` hash
    in the trace remains for provenance.
    """
    safe = json.loads(json.dumps(payload))
    safe.pop("prompt_asset", None)
    generation_type = str(safe.get("generation_type") or "")
    if generation_type == "flashcards":
        for item in safe.get("result", {}).get("items", []):
            if not isinstance(item, dict):
                continue
            item.pop("back", None)
            item.pop("answer", None)
        return safe
    if generation_type != "quiz":
        return safe
    for item in safe.get("result", {}).get("items", []):
        if not isinstance(item, dict):
            continue
        item.pop("correct_answer", None)
        item.pop("is_correct", None)
        # Quiz explanations are returned by the grading endpoint after an
        # attempt; generated explanations may begin with the answer itself.
        item.pop("explanation", None)
        for option in item.get("options", []):
            if isinstance(option, dict):
                option.pop("is_correct", None)
    return safe


def _learner_safe_task_result(result: GenerationResult) -> dict[str, Any]:
    return _learner_safe_task_result_dict(result.to_dict())


def _task_result_with_page_schema(
    result: GenerationResult, generation_id: str, *, released: bool
) -> dict[str, Any]:
    """Attach the persisted PageSchema for released artifacts.

    An unreleased (``needs_review``) result must never become a published
    PageSchema (invariants #8/#11): the learner may preview the raw artifact
    during review, but the immutable/frozen page is materialized only once the
    task reaches ``completed``. Retry reuses the generation id, so gating on
    ``released`` also prevents a cached pre-retry page from masking a
    regeneration (the post-retry ``completed`` result re-projects).
    """
    safe = _learner_safe_task_result(result)
    if not released:
        return safe

    page_schema_id = f"{generation_id}:page"
    store = PageStore()
    page_schema = store.get(page_schema_id)
    if page_schema is None:
        # Validation fallbacks deliberately carry a ``:degrade`` identity;
        # recover that immutable published candidate instead of re-projecting.
        page_schema = store.get(f"{page_schema_id}:degrade")
    if page_schema is not None:
        safe["page_schema"] = page_schema.model_dump(mode="json")
        safe["page_schema_id"] = page_schema.page_schema_id
    return safe


def _reject_unsafe_material(material: object) -> None:
    """Do not send document instructions to the material-analysis model."""
    action, _category = scan_untrusted_learning_payload(material)
    if action == "block":
        raise HTTPException(
            status_code=422,
            detail="Please remove instruction-like content from the material before analysis.",
        )


class MaterialSourceRequest(BaseModel):
    source_type: Literal["knowledge", "notebook", "upload", "paste"] = "paste"
    text: str = ""
    title: str = "Untitled material"
    source_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class GenerateSuiteRequest(BaseModel):
    generation_type: Literal["courseware", "flashcards", "quiz"]
    material: MaterialSourceRequest
    learner_profile: dict[str, Any] = Field(default_factory=dict)
    options: dict[str, Any] = Field(default_factory=dict)


class QuizAnswerRequest(BaseModel):
    """A learner answer; answer keys remain inside the server-owned task."""

    question_id: str = Field(min_length=1, max_length=256)
    answer: str = Field(max_length=20_000)
    attempt_id: str | None = Field(default=None, min_length=1, max_length=256)


def _generation_subject_id(result: GenerationResult) -> str:
    """Recover the server-owned material subject used for this generation."""
    for event in reversed(getattr(result, "events", []) or []):
        if not isinstance(event, dict):
            continue
        data_value = event.get("data")
        data: dict[str, Any] = data_value if isinstance(data_value, dict) else {}
        subject_value = data.get("subject_ref")
        subject: dict[str, Any] = subject_value if isinstance(subject_value, dict) else {}
        subject_id = str(subject.get("subject_id") or "").strip()
        if subject_id:
            return subject_id
    return ""


def _record_canonical_generation_quiz_answer(
    *,
    result: GenerationResult,
    item: dict[str, Any],
    correct: bool,
    attempt_id: str,
    chain: CanonicalAnswerEventChain | None = None,
    learning_service: LearningService | None = None,
    learning_path_id: str | None = None,
    user_answer: str = "",
) -> None:
    """Record a standalone Quiz answer before any BKT projection.

    Generated item IDs and material subjects are server-owned. Missing either
    still produces an auditable attribution-pending event, never strong BKT
    evidence.
    """
    subject_id = _generation_subject_id(result)
    kc_id = str(item.get("node_id") or "").strip()
    event, _outcome = (chain or CanonicalAnswerEventChain()).record_server_graded(
        user_id=get_current_user().id,
        subject_id=subject_id,
        question_id=str(item.get("question_id") or ""),
        kc_ids=(kc_id,) if kc_id else (),
        is_correct=correct,
        item_valid=bool(str(item.get("correct_answer") or "").strip()),
        attribution_reliable=bool(subject_id and kc_id),
        derived=lambda recorded: project_canonical_event_to_existing_progress(
            recorded,
            service=learning_service,
        ),
        attempt_id=attempt_id,
        surface_type="quiz",
        # A standalone generation has no implicit LearningProgress mapping.
        # Callers may pass a server-owned mapping when one exists; do not turn
        # client material metadata or a generation ID into a book ID by guess.
        learning_path_id=learning_path_id,
        error_tag=(classify_error(user_answer).value if not correct else None),
    )
    if event.page_id != str(item.get("question_id") or "") or event.answer_correct != correct:
        raise ValueError("attempt_id cannot be reused for a different Quiz answer")


class PrepareMaterialRequest(BaseModel):
    filename: str
    base64: str
    mime_type: str | None = None

    @model_validator(mode="after")
    def _bound_image_payload(self) -> "PrepareMaterialRequest":
        if (
            is_image_material_candidate(self.filename, self.mime_type)
            and len(self.base64) > MAX_IMAGE_BASE64_CHARS
        ):
            raise ValueError("Image material exceeds the 10 MB upload limit")
        return self


class AnalyzeMaterialRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=160)
    material: MaterialSourceRequest

    @model_validator(mode="after")
    def _bound_analysis_payload(self) -> "AnalyzeMaterialRequest":
        """Reject oversized paste/upload metadata before material resolution or LLM use."""
        if len(self.material.text) > ANALYSIS_MAX_TEXT_CHARS:
            raise ValueError(
                f"material.text must be at most {ANALYSIS_MAX_TEXT_CHARS} characters for analysis"
            )
        try:
            metadata_size = len(json.dumps(self.material.metadata, ensure_ascii=False))
        except (TypeError, ValueError) as exc:
            raise ValueError("material.metadata must be JSON serializable") from exc
        if metadata_size > ANALYSIS_MAX_METADATA_CHARS:
            raise ValueError(
                f"material.metadata is too large for analysis (max {ANALYSIS_MAX_METADATA_CHARS} characters)"
            )
        page_slices = self.material.metadata.get("page_slices")
        if page_slices is not None:
            if not isinstance(page_slices, list):
                raise ValueError("material.metadata.page_slices must be a list")
            if len(page_slices) > ANALYSIS_MAX_PAGE_SLICES:
                raise ValueError(
                    f"material.metadata.page_slices must contain at most {ANALYSIS_MAX_PAGE_SLICES} pages"
                )
            for index, page in enumerate(page_slices, start=1):
                if (
                    not isinstance(page, dict)
                    or len(str(page.get("text") or "")) > ANALYSIS_MAX_PAGE_SLICE_CHARS
                ):
                    raise ValueError(
                        f"material.metadata.page_slices[{index}] text must be at most {ANALYSIS_MAX_PAGE_SLICE_CHARS} characters"
                    )
        return self


@router.post("/materials/prepare")
async def prepare_material(request: PrepareMaterialRequest):
    """Turn a browser-selected document or image into traceable material."""
    import base64

    try:
        data = base64.b64decode(request.base64, validate=True)
        if is_image_material_candidate(request.filename, request.mime_type):
            return await prepare_learning_image(
                request.filename,
                data,
                mime_type=request.mime_type,
                owner_id=get_current_user().id,
            )
        prepared = prepare_learning_document(request.filename, data, mime_type=request.mime_type)
    except LearningImageUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": exc.code, "message": str(exc), "capability": "image_ocr"},
        ) from exc
    except LearningImageError as exc:
        status_code = 502 if exc.code == "image_ocr_failed" else 422
        if exc.code == "image_source_storage_failed":
            status_code = 503
        raise HTTPException(
            status_code=status_code,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    except (ValueError, LearningDocumentError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "source_type": "upload",
        "source_id": f"material-{uuid4().hex}",
        "title": prepared["filename"],
        "text": "",
        "metadata": prepared,
    }


@router.post("/materials/analyze")
async def analyze_prepared_material(request: AnalyzeMaterialRequest):
    """Classify page-scoped material and persist the immutable session record."""
    material = _canonical_material_payload(request.material)
    _reject_unsafe_material(material)
    try:
        analysis = await analyze_material(MaterialSource(**material), session_id=request.session_id)
        return analysis.to_dict()
    except GenerationConfigurationError as exc:
        raise HTTPException(
            status_code=409, detail=f"Configure a generation model before continuing: {exc}"
        ) from exc
    except MaterialAnalysisRateLimitError as exc:
        raise HTTPException(
            status_code=429, detail=str(exc), headers={"Retry-After": str(60)}
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/materials/analyses/{session_id}/{analysis_id}")
async def get_material_analysis(session_id: str, analysis_id: str):
    try:
        return load_material_analysis(analysis_id, session_id, enforce_owner=True)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Material analysis not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _to_generation_request(request: GenerateSuiteRequest) -> GenerationRequest:
    learning_component = request.options.get("learning_component")
    if (
        isinstance(learning_component, dict)
        and str(learning_component.get("component_type") or "") == "calibration_checkpoint"
    ):
        raise HTTPException(
            status_code=422,
            detail=(
                "calibration_checkpoint is deterministic; complete it through the "
                "learning component event, which aggregates the round's evidence "
                "into a progress calibration instead of generating a new component."
            ),
        )
    # Standalone tools are still learning surfaces.  Do not let an integration
    # bypass the material-analysis endpoint and feed a document instruction to
    # a generation model directly.
    material = _canonical_material_payload(request.material)
    _reject_unsafe_material(material)
    # Research evidence may only be created by the owner-bound Research
    # Workspace route.  Its server-only dataclass field is durable through the
    # queue, but public generic generation must neither accept nor echo a
    # browser-supplied provenance-shaped marker.
    if (
        "research_courseware_provenance" in request.options
        or "research_courseware_provenance" in material.get("metadata", {})
    ):
        raise HTTPException(
            status_code=422,
            detail="Research provenance must be submitted from Research Workspace.",
        )
    return GenerationRequest(
        generation_type=request.generation_type,
        material=MaterialSource(**material),
        learner_profile=request.learner_profile,
        options=request.options,
    )


def _canonical_material_payload(request: MaterialSourceRequest) -> dict[str, Any]:
    """Resolve image OCR from private owner truth before analysis/generation."""
    payload = request.model_dump()
    try:
        return canonical_prepared_image_material(payload, owner_id=get_current_user().id)
    except LearningImageError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc


def _task_submission(task):
    return {
        "generation_id": task.generation_id,
        "status": task.status,
        "events_url": f"/api/v1/traittutor/generate/tasks/{task.generation_id}/events",
        "result_url": f"/api/v1/traittutor/generate/tasks/{task.generation_id}",
    }


@router.post("/tasks", status_code=status.HTTP_202_ACCEPTED)
async def create_generation_task(request: GenerateSuiteRequest):
    return _task_submission(get_generation_task_manager().create(_to_generation_request(request)))


@router.get("/tasks/{generation_id}/events")
async def stream_generation_events(
    generation_id: str,
    after_seq: int = 0,
    last_event_id: str | None = Header(default=None),
):
    manager = get_generation_task_manager()
    if manager.get(generation_id) is None:
        raise HTTPException(status_code=404, detail="TraitTutor generation task not found")

    # Browsers cannot set Last-Event-ID explicitly on a fresh EventSource, so
    # the canonical client may provide after_seq for an exact durable replay.
    resume_after = after_seq
    if last_event_id is not None:
        try:
            resume_after = max(0, int(last_event_id))
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail="Last-Event-ID must be a non-negative integer"
            ) from exc

    async def event_stream():
        async for event in manager.events_after(generation_id, resume_after):
            yield f"id: {event['sequence']}\nevent: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/tasks/{generation_id}")
async def get_generation_task(generation_id: str):
    task = get_generation_task_manager().get(generation_id)
    if task is None:
        raise HTTPException(status_code=404, detail="TraitTutor generation task not found")
    if task.result is not None and task.status != "discarded":
        return _task_result_with_page_schema(
            task.result, generation_id, released=task.status == "completed"
        )
    return {
        "generation_id": generation_id,
        "status": task.status,
        "error": task.error,
        "error_code": task.error_code,
        "retryable": task.retryable,
        "cancellation_requested": task.cancel_requested,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
    }


@router.post("/tasks/{generation_id}/quiz/grade")
async def grade_generation_quiz_answer(generation_id: str, request: QuizAnswerRequest):
    """Grade one standalone Quiz answer without disclosing its answer key.

    Only released (``completed``) quizzes are gradable (invariants #5/#8): a
    ``needs_review`` artifact is unreleased, and the explanation returned here
    may begin with the answer, so grading it would surface the key before
    review/confirmation. The server-owned verdict is written to
    ``LearnerEvent`` before BKT projection.
    """
    task = get_generation_task_manager().get(generation_id)
    if task is None or task.result is None or task.status != "completed":
        raise HTTPException(status_code=404, detail="TraitTutor quiz task not found")
    if task.result.generation_type != "quiz":
        raise HTTPException(status_code=409, detail="This generation task is not a quiz")
    items = task.result.result.get("items", []) if isinstance(task.result.result, dict) else []
    item = next(
        (
            entry
            for entry in items
            if isinstance(entry, dict)
            and str(entry.get("question_id") or "") == request.question_id
        ),
        None,
    )
    if not isinstance(item, dict):
        raise HTTPException(status_code=404, detail="Quiz question not found")
    expected = str(item.get("correct_answer") or "")
    if not expected:
        raise HTTPException(status_code=422, detail="Quiz question has no server-verifiable answer")
    correct = grade_answer(request.answer, expected, str(item.get("question_type") or "short"))
    response = {
        "question_id": request.question_id,
        "correct": correct,
        "explanation": str(item.get("explanation") or ""),
    }
    attempt_id = request.attempt_id or f"attempt_{uuid4().hex}"
    try:
        _record_canonical_generation_quiz_answer(
            result=task.result,
            item=item,
            correct=correct,
            attempt_id=attempt_id,
            user_answer=request.answer,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    response["attempt_id"] = attempt_id
    return response


@router.post("/tasks/{generation_id}/flashcards/{card_id}/reveal")
async def reveal_generation_flashcard_answer(generation_id: str, card_id: str):
    """Reveal one released flashcard answer after an explicit learner action."""
    task = get_generation_task_manager().get(generation_id)
    if task is None or task.result is None or task.status != "completed":
        raise HTTPException(status_code=404, detail="TraitTutor flashcard task not found")
    if task.result.generation_type != "flashcards":
        raise HTTPException(status_code=409, detail="This generation task is not flashcards")
    items = task.result.result.get("items", []) if isinstance(task.result.result, dict) else []
    item = next(
        (
            entry
            for entry in items
            if isinstance(entry, dict)
            and str(entry.get("node_id") or entry.get("question_id") or "") == card_id
        ),
        None,
    )
    if not isinstance(item, dict):
        raise HTTPException(status_code=404, detail="Flashcard not found")
    answer = str(item.get("back") or item.get("answer") or "").strip()
    if not answer:
        raise HTTPException(status_code=422, detail="Flashcard has no server-held answer")
    return {"card_id": card_id, "answer": answer}


@router.post("/tasks/{generation_id}/retry", status_code=status.HTTP_202_ACCEPTED)
async def retry_generation_task(generation_id: str):
    manager = get_generation_task_manager()
    existing = manager.get(generation_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="TraitTutor generation task not found")
    if not existing.retryable and existing.status != "needs_review":
        raise HTTPException(
            status_code=409,
            detail="Only failed, interrupted, or review-required generation tasks can be retried",
        )
    task = manager.retry(generation_id)
    assert task is not None
    return {
        "generation_id": task.generation_id,
        "status": task.status,
        "events_url": f"/api/v1/traittutor/generate/tasks/{task.generation_id}/events",
        "result_url": f"/api/v1/traittutor/generate/tasks/{task.generation_id}",
    }


@router.post("/tasks/{generation_id}/review/confirm")
async def confirm_generation_review(generation_id: str):
    manager = get_generation_task_manager()
    existing = manager.get(generation_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="TraitTutor generation task not found")
    if existing.status != "needs_review":
        raise HTTPException(
            status_code=409, detail="Only generation tasks awaiting review can be confirmed"
        )
    task = manager.confirm_review(generation_id)
    assert task is not None
    if task.status != "completed" or task.result is None:
        raise HTTPException(
            status_code=409, detail="Only generation tasks awaiting review can be confirmed"
        )
    return _learner_safe_task_result(task.result)


@router.post("/tasks/{generation_id}/review/discard")
async def discard_generation_review(generation_id: str):
    manager = get_generation_task_manager()
    existing = manager.get(generation_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="TraitTutor generation task not found")
    if existing.status != "needs_review":
        raise HTTPException(
            status_code=409, detail="Only generation tasks awaiting review can be discarded"
        )
    task = manager.discard_review(generation_id)
    assert task is not None
    if task.status != "discarded":
        raise HTTPException(
            status_code=409, detail="Only generation tasks awaiting review can be discarded"
        )
    return {"generation_id": task.generation_id, "status": task.status}


@router.delete("/tasks/{generation_id}", status_code=status.HTTP_202_ACCEPTED)
async def cancel_generation_task(generation_id: str):
    task = get_generation_task_manager().cancel(generation_id)
    if task is None:
        raise HTTPException(status_code=404, detail="TraitTutor generation task not found")
    if task.completed and task.status != "cancelled":
        raise HTTPException(
            status_code=409, detail="Completed generation tasks cannot be cancelled"
        )
    return {
        "generation_id": task.generation_id,
        "status": task.status,
        "cancellation_requested": task.cancel_requested,
    }


@router.get("/generations")
async def get_generations():
    generations = list_generations()
    return {
        "generations": [_learner_safe_task_result_dict(item) for item in generations],
        "total": len(generations),
    }


@router.get("/generations/{generation_id}")
async def get_generation(generation_id: str):
    try:
        return _learner_safe_task_result_dict(load_generation(generation_id))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="TraitTutor generation not found") from exc
