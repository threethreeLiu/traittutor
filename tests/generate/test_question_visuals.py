"""Difficulty-triggered image function calls for generated quiz items."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from traittutor.generate import visuals


@pytest.mark.asyncio
async def test_question_asset_key_creates_a_distinct_output_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class Paths:
        def get_task_workspace(self, _kind: str, task_id: str) -> Path:
            return tmp_path / task_id

        def get_public_outputs_root(self) -> Path:
            return tmp_path

    async def image(*_args: Any, **_kwargs: Any) -> list[tuple[bytes, str]]:
        return [(b"png", "image/png")]

    monkeypatch.setattr(visuals, "get_path_service", Paths)
    monkeypatch.setattr(visuals, "generate_image", image)

    first = await visuals.generate_learning_visual(
        {"kind": "quiz", "title": "Q1", "items": [{"question": "Q1"}], "asset_key": "question-1"},
        generation_id="quiz-gen",
    )
    second = await visuals.generate_learning_visual(
        {"kind": "quiz", "title": "Q2", "items": [{"question": "Q2"}], "asset_key": "question-2"},
        generation_id="quiz-gen",
    )

    assert first["asset"]["url"].endswith("learning-visual-question-1.png")
    assert second["asset"]["url"].endswith("learning-visual-question-2.png")
    assert first["asset"]["url"] != second["asset"]["url"]


@pytest.mark.asyncio
async def test_hard_questions_call_image_function_and_bind_assets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    async def generate(prompt_source: dict[str, Any], *, generation_id: str) -> dict[str, Any]:
        calls.append({"prompt_source": prompt_source, "generation_id": generation_id})
        asset_key = str(prompt_source["asset_key"])
        return {
            "status": "completed",
            "asset": {"url": f"/api/outputs/{asset_key}.png", "alt": "Question diagram"},
        }

    monkeypatch.setattr(visuals, "generate_learning_visual", generate)
    result: dict[str, Any] = {
        "kind": "quiz",
        "title": "Mechanics quiz",
        "items": [
            {
                "question_id": 1,
                "node_id": "force",
                "node_name": "Force composition",
                "question": "Which resultant follows from the two forces?",
                "difficulty": "hard",
            },
            {
                "question_id": 2,
                "node_id": "units",
                "node_name": "Units",
                "question": "What is the SI unit of force?",
                "difficulty": "easy",
            },
            {
                "question_id": 3,
                "node_id": "motion",
                "node_name": "Motion graph",
                "question": "Infer the acceleration across the intervals.",
                "difficulty": "hard",
            },
        ],
    }

    trace = await visuals.attach_hard_question_visuals(result, generation_id="quiz-gen")

    assert trace == {
        "status": "completed",
        "reason": "hard_question_difficulty",
        "requested": 2,
        "completed": 2,
        "questions": [
            {"question_id": "1", "difficulty": "hard", "status": "completed"},
            {"question_id": "3", "difficulty": "hard", "status": "completed"},
        ],
    }
    assert [call["prompt_source"]["asset_key"] for call in calls] == [
        "question-1",
        "question-3",
    ]
    assert result["items"][0]["images"][0]["url"].endswith("question-1.png")
    assert "images" not in result["items"][1]
    assert result["items"][2]["images"][0]["url"].endswith("question-3.png")


@pytest.mark.asyncio
async def test_hard_question_image_failure_degrades_without_dropping_question(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"status": "failed", "message": "provider unavailable"}

    monkeypatch.setattr(visuals, "generate_learning_visual", fail)
    item = {
        "question_id": 7,
        "node_id": "proof",
        "node_name": "Proof",
        "question": "Prove the relation.",
        "difficulty": "hard",
    }
    result: dict[str, Any] = {"kind": "quiz", "title": "Quiz", "items": [item]}

    trace = await visuals.attach_hard_question_visuals(result, generation_id="quiz-failed")

    assert trace["status"] == "degraded"
    assert trace["completed"] == 0
    assert trace["questions"][0]["message"] == "provider unavailable"
    assert result["items"] == [item]
    assert "images" not in item


@pytest.mark.asyncio
async def test_non_hard_questions_do_not_call_image_function(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unexpected(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("image generation must only run for hard questions")

    monkeypatch.setattr(visuals, "generate_learning_visual", unexpected)
    result: dict[str, Any] = {
        "kind": "quiz",
        "title": "Quiz",
        "items": [{"question_id": 1, "question": "Recall this.", "difficulty": "medium"}],
    }

    trace = await visuals.attach_hard_question_visuals(result, generation_id="quiz-medium")

    assert trace == {"status": "skipped", "reason": "no_hard_questions", "questions": []}
