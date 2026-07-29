"""TraitTutor Generate Suite API."""

from __future__ import annotations

from typing import Any, Literal

import json

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from traittutor.generate.service import (
    GenerationRequest,
    MaterialSource,
    generate_traittutor_content,
    list_generations,
    load_generation,
    save_generation,
)
from traittutor.generate.tasks import get_generation_task_manager

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


def _to_generation_request(request: GenerateSuiteRequest) -> GenerationRequest:
    return GenerationRequest(
        generation_type=request.generation_type,
        material=MaterialSource(**request.material.model_dump()),
        learner_profile=request.learner_profile,
        options=request.options,
    )


@router.post("")
async def generate_suite(request: GenerateSuiteRequest):
    try:
        result = generate_traittutor_content(request=_to_generation_request(request))
        save_generation(result)
        return result.to_dict()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/tasks", status_code=status.HTTP_202_ACCEPTED)
async def create_generation_task(request: GenerateSuiteRequest):
    task = get_generation_task_manager().create(_to_generation_request(request))
    return {
        "generation_id": task.generation_id,
        "status": "queued",
        "events_url": f"/api/v1/traittutor/generate/tasks/{task.generation_id}/events",
        "result_url": f"/api/v1/traittutor/generate/tasks/{task.generation_id}",
    }


@router.get("/tasks/{generation_id}/events")
async def stream_generation_events(generation_id: str, after_seq: int = 0):
    manager = get_generation_task_manager()
    if manager.get(generation_id) is None:
        raise HTTPException(status_code=404, detail="TraitTutor generation task not found")

    async def event_stream():
        async for event in manager.events_after(generation_id, after_seq):
            yield f"id: {event['sequence']}\nevent: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/tasks/{generation_id}")
async def get_generation_task(generation_id: str):
    task = get_generation_task_manager().get(generation_id)
    if task is None:
        raise HTTPException(status_code=404, detail="TraitTutor generation task not found")
    if task.result is not None:
        return task.result.to_dict()
    return {"generation_id": generation_id, "status": "failed" if task.error else "running", "error": task.error}


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
