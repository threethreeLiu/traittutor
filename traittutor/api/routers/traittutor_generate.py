"""TraitTutor Generate Suite API."""

from __future__ import annotations

from typing import Any, Literal
from uuid import uuid4

import json

from fastapi import APIRouter, Header, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, model_validator

from traittutor.generate.service import GenerationRequest, MaterialSource, list_generations, load_generation
from traittutor.generate.tasks import get_generation_task_manager
from traittutor.generate.runner import GenerationConfigurationError
from traittutor.generate.document_material import LearningDocumentError, prepare_learning_document
from traittutor.generate.material_analysis import (
    ANALYSIS_MAX_METADATA_CHARS,
    ANALYSIS_MAX_PAGE_SLICE_CHARS,
    ANALYSIS_MAX_PAGE_SLICES,
    ANALYSIS_MAX_TEXT_CHARS,
    MaterialAnalysisRateLimitError,
    analyze_material,
    load_material_analysis,
)

router = APIRouter()


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


class PrepareMaterialRequest(BaseModel):
    filename: str
    base64: str
    mime_type: str | None = None


class AnalyzeMaterialRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=160)
    material: MaterialSourceRequest

    @model_validator(mode="after")
    def _bound_analysis_payload(self) -> "AnalyzeMaterialRequest":
        """Reject oversized paste/upload metadata before material resolution or LLM use."""
        if len(self.material.text) > ANALYSIS_MAX_TEXT_CHARS:
            raise ValueError(f"material.text must be at most {ANALYSIS_MAX_TEXT_CHARS} characters for analysis")
        try:
            metadata_size = len(json.dumps(self.material.metadata, ensure_ascii=False))
        except (TypeError, ValueError) as exc:
            raise ValueError("material.metadata must be JSON serializable") from exc
        if metadata_size > ANALYSIS_MAX_METADATA_CHARS:
            raise ValueError(f"material.metadata is too large for analysis (max {ANALYSIS_MAX_METADATA_CHARS} characters)")
        page_slices = self.material.metadata.get("page_slices")
        if page_slices is not None:
            if not isinstance(page_slices, list):
                raise ValueError("material.metadata.page_slices must be a list")
            if len(page_slices) > ANALYSIS_MAX_PAGE_SLICES:
                raise ValueError(f"material.metadata.page_slices must contain at most {ANALYSIS_MAX_PAGE_SLICES} pages")
            for index, page in enumerate(page_slices, start=1):
                if not isinstance(page, dict) or len(str(page.get("text") or "")) > ANALYSIS_MAX_PAGE_SLICE_CHARS:
                    raise ValueError(f"material.metadata.page_slices[{index}] text must be at most {ANALYSIS_MAX_PAGE_SLICE_CHARS} characters")
        return self


@router.post("/materials/prepare")
async def prepare_material(request: PrepareMaterialRequest):
    """Turn a browser-selected document into page-scoped generation material."""
    import base64
    try:
        data = base64.b64decode(request.base64, validate=True)
        prepared = prepare_learning_document(request.filename, data, mime_type=request.mime_type)
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
    try:
        analysis = await analyze_material(MaterialSource(**request.material.model_dump()), session_id=request.session_id)
        return analysis.to_dict()
    except GenerationConfigurationError as exc:
        raise HTTPException(status_code=409, detail=f"Configure a generation model before continuing: {exc}") from exc
    except MaterialAnalysisRateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc), headers={"Retry-After": str(60)}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/materials/analyses/{session_id}/{analysis_id}")
async def get_material_analysis(session_id: str, analysis_id: str):
    try:
        return load_material_analysis(analysis_id, session_id, enforce_owner=True)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Material analysis not found") from exc


def _to_generation_request(request: GenerateSuiteRequest) -> GenerationRequest:
    return GenerationRequest(
        generation_type=request.generation_type,
        material=MaterialSource(**request.material.model_dump()),
        learner_profile=request.learner_profile,
        options=request.options,
    )


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def generate_suite(request: GenerateSuiteRequest):
    """Legacy submission URL, now routed through the durable task queue.

    Returning a task handle rather than synchronously invoking the provider
    prevents this older public path from bypassing shared limits and auditing.
    """
    return _task_submission(get_generation_task_manager().create(_to_generation_request(request)))


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

    # Standard SSE reconnection semantics: the browser's Last-Event-ID takes
    # precedence over a legacy query parameter when both are supplied.
    resume_after = after_seq
    if last_event_id is not None:
        try:
            resume_after = max(0, int(last_event_id))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Last-Event-ID must be a non-negative integer") from exc

    async def event_stream():
        async for event in manager.events_after(generation_id, resume_after):
            yield f"id: {event['sequence']}\nevent: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/tasks/{generation_id}")
async def get_generation_task(generation_id: str):
    task = get_generation_task_manager().get(generation_id)
    if task is None:
        raise HTTPException(status_code=404, detail="TraitTutor generation task not found")
    if task.result is not None:
        return task.result.to_dict()
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


@router.post("/tasks/{generation_id}/retry", status_code=status.HTTP_202_ACCEPTED)
async def retry_generation_task(generation_id: str):
    manager = get_generation_task_manager()
    existing = manager.get(generation_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="TraitTutor generation task not found")
    if not existing.retryable:
        raise HTTPException(status_code=409, detail="Only failed or interrupted generation tasks can be retried")
    task = manager.retry(generation_id)
    assert task is not None
    return {
        "generation_id": task.generation_id,
        "status": task.status,
        "events_url": f"/api/v1/traittutor/generate/tasks/{task.generation_id}/events",
        "result_url": f"/api/v1/traittutor/generate/tasks/{task.generation_id}",
    }


@router.delete("/tasks/{generation_id}", status_code=status.HTTP_202_ACCEPTED)
async def cancel_generation_task(generation_id: str):
    task = get_generation_task_manager().cancel(generation_id)
    if task is None:
        raise HTTPException(status_code=404, detail="TraitTutor generation task not found")
    if task.completed and task.status != "cancelled":
        raise HTTPException(status_code=409, detail="Completed generation tasks cannot be cancelled")
    return {"generation_id": task.generation_id, "status": task.status, "cancellation_requested": task.cancel_requested}


@router.get("/generations")
async def get_generations():
    generations = list_generations()
    return {"generations": generations, "total": len(generations)}


@router.get("/generations/{generation_id}")
async def get_generation(generation_id: str):
    try:
        return load_generation(generation_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="TraitTutor generation not found") from exc
