from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from traittutor.api.routers import traittutor_generate as generate_router
from traittutor.components import (
    ComponentInstance,
    PageRegion,
    PageSchema,
    PageStore,
    validate_page_schema,
)
from traittutor.generate.service import GenerationRequest, GenerationResult, MaterialSource
from traittutor.generate.tasks import GenerationTask

CREATED = "2026-08-09T08:00:00+00:00"


def _task() -> GenerationTask:
    result = GenerationResult(
        generation_id="page-task",
        generation_type="courseware",
        status="completed",
        events=[],
        result={
            "kind": "courseware",
            "sections": [{"section_title": "One", "core_content": ["Body"]}],
        },
        created_at=CREATED,
        prompt_asset="courseware.md",
        material={},
        learner_profile={},
    )
    return GenerationTask(
        generation_id="page-task",
        owner_id="learner",
        request=GenerationRequest(
            generation_type="courseware",
            material=MaterialSource(source_type="paste", text="Material"),
        ),
        status="completed",
        result=result,
    )


class _Manager:
    def __init__(self, task: GenerationTask) -> None:
        self.task = task

    def get(self, generation_id: str) -> GenerationTask | None:
        return self.task if generation_id == self.task.generation_id else None


def test_canonical_page_schema_embeds_and_recovers_persisted_page(
    monkeypatch: Any, tmp_path: Path
) -> None:
    task = _task()
    store = PageStore(path=tmp_path / "pages.json")
    page = PageSchema(
        page_schema_id=f"{task.generation_id}:page",
        generation_run_id=task.generation_id,
        version="v1",
        regions=[
            PageRegion(
                region_id="r1",
                component=ComponentInstance(
                    instance_id=f"{task.generation_id}:page:r1",
                    component_type="concept_explanation",
                    version="v1",
                    props={"title": "One", "body_markdown": "Body"},
                ),
            )
        ],
        created_at=task.result.created_at,
    )
    store.save(page)
    monkeypatch.setattr(generate_router, "get_generation_task_manager", lambda: _Manager(task))
    monkeypatch.setattr(generate_router, "PageStore", lambda: store)

    first = asyncio.run(generate_router.get_generation_task(task.generation_id))
    embedded = PageSchema.model_validate(first["page_schema"])
    validate_page_schema(embedded)
    assert first["page_schema_id"] == "page-task:page"
    assert "answer" not in str(first["page_schema"]).lower()

    second = asyncio.run(generate_router.get_generation_task(task.generation_id))
    assert second["page_schema_id"] == first["page_schema_id"]
    assert second["page_schema"] == first["page_schema"]


def test_old_generation_submission_route_is_absent() -> None:
    assert all(route.path != "" for route in generate_router.router.routes)


def test_flashcard_task_projection_keeps_answers_server_side() -> None:
    public = generate_router._learner_safe_task_result_dict(
        {
            "generation_id": "flashcards-1",
            "generation_type": "flashcards",
            "prompt_asset": "flashcards/internal.md",
            "result": {
                "kind": "flashcards",
                "items": [
                    {
                        "node_id": "concept-1",
                        "front": "What is the rule?",
                        "back": "The server-held answer.",
                        "answer": "A legacy server-held answer.",
                    }
                ],
            },
        }
    )

    assert "prompt_asset" not in public
    assert public["result"]["items"] == [{"node_id": "concept-1", "front": "What is the rule?"}]


def test_flashcard_answer_is_revealed_only_for_one_completed_card(monkeypatch: Any) -> None:
    result = GenerationResult(
        generation_id="flashcards-1",
        generation_type="flashcards",
        status="completed",
        events=[],
        result={
            "kind": "flashcards",
            "items": [
                {
                    "node_id": "concept-1",
                    "front": "What is the rule?",
                    "back": "The server-held answer.",
                }
            ],
        },
        created_at=CREATED,
        prompt_asset="flashcards/internal.md",
        material={},
        learner_profile={},
    )
    task = GenerationTask(
        generation_id="flashcards-1",
        owner_id="learner",
        request=GenerationRequest(
            generation_type="flashcards",
            material=MaterialSource(source_type="paste", text="Material"),
        ),
        status="completed",
        result=result,
    )
    monkeypatch.setattr(generate_router, "get_generation_task_manager", lambda: _Manager(task))

    revealed = asyncio.run(
        generate_router.reveal_generation_flashcard_answer("flashcards-1", "concept-1")
    )

    assert revealed == {"card_id": "concept-1", "answer": "The server-held answer."}
