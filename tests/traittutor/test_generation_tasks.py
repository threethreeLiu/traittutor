from __future__ import annotations

import asyncio

import pytest

from traittutor.generate.service import GenerationRequest, GenerationResult, MaterialSource
from traittutor.generate.tasks import GenerationTaskManager


def _request() -> GenerationRequest:
    return GenerationRequest("flashcards", MaterialSource("paste", "材料", "标题"))


@pytest.mark.asyncio
async def test_task_emits_accepted_before_background_generation():
    def generator(_request: GenerationRequest) -> GenerationResult:
        return GenerationResult("internal", "flashcards", "completed", [{"type": "batch_validated", "data": {"count": 1}}], {"items": []}, "now", "prompt", {}, {})

    manager = GenerationTaskManager(generator)
    task = manager.create(_request())
    assert task.events[0]["type"] == "accepted"

    events = [event async for event in manager.events_after(task.generation_id)]
    assert [event["type"] for event in events][:4] == ["accepted", "material_resolved", "profile_strategy_ready", "generation_started"]
    assert events[-1]["type"] == "completed"
