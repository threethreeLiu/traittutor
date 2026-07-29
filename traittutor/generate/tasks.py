"""In-process task and SSE event store for TraitTutor generation."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable
from uuid import uuid4

from .service import GenerationRequest, GenerationResult, generate_traittutor_content, generate_traittutor_content_async, save_generation


@dataclass
class GenerationTask:
    generation_id: str
    events: list[dict[str, Any]] = field(default_factory=list)
    result: GenerationResult | None = None
    error: str | None = None
    completed: bool = False
    wake: asyncio.Event = field(default_factory=asyncio.Event)

    def emit(self, event_type: str, message: str, **data: Any) -> None:
        self.events.append({"sequence": len(self.events) + 1, "type": event_type, "message": message, "data": data})
        self.wake.set()


class GenerationTaskManager:
    """Small task registry whose event contract stays stable across runners."""

    _instance: "GenerationTaskManager | None" = None

    def __init__(self, generator: Callable[[GenerationRequest], GenerationResult] = generate_traittutor_content):
        self._generator = generator
        self._tasks: dict[str, GenerationTask] = {}

    @classmethod
    def get_instance(cls) -> "GenerationTaskManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def create(self, request: GenerationRequest) -> GenerationTask:
        task = GenerationTask(generation_id=uuid4().hex)
        self._tasks[task.generation_id] = task
        task.emit("accepted", "Generation request accepted", generation_id=task.generation_id)
        asyncio.create_task(self._run(task, request))
        return task

    async def _run(self, task: GenerationTask, request: GenerationRequest) -> None:
        try:
            task.emit("material_resolved", "Material is ready for generation", source_type=request.material.source_type)
            task.emit("profile_strategy_ready", "Learner teaching strategy is ready")
            task.emit("generation_started", "Generating structured learning content", generation_type=request.generation_type)
            if self._generator is generate_traittutor_content:
                result = await generate_traittutor_content_async(request)
            else:
                result = await asyncio.to_thread(self._generator, request)
            for event in result.events:
                if event.get("type") == "batch_validated":
                    task.emit("batch_validated", "Structured output batch validated", **dict(event.get("data") or {}))
            save_generation(result)
            task.result = result
            task.emit("completed", "Generation completed", result_url=f"/generations/{task.generation_id}")
        except Exception as exc:  # user-facing event; detailed trace stays server-side
            task.error = str(exc)
            task.emit("failed", "Generation failed", code="generation_failed", retryable=True)
        finally:
            task.completed = True
            task.wake.set()

    def get(self, generation_id: str) -> GenerationTask | None:
        return self._tasks.get(generation_id)

    async def events_after(self, generation_id: str, after_sequence: int = 0):
        task = self.get(generation_id)
        if task is None:
            raise KeyError(generation_id)
        next_sequence = after_sequence + 1
        while True:
            available = [event for event in task.events if event["sequence"] >= next_sequence]
            for event in available:
                next_sequence = event["sequence"] + 1
                yield event
            if task.completed:
                return
            task.wake.clear()
            await task.wake.wait()


def get_generation_task_manager() -> GenerationTaskManager:
    return GenerationTaskManager.get_instance()


__all__ = ["GenerationTask", "GenerationTaskManager", "get_generation_task_manager"]
