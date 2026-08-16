from __future__ import annotations

from typing import Any, Mapping

import pytest

from traittutor.generate.catalog import PromptDefinition, load_prompt
from traittutor.generate.courseware import _lesson_schema, generate_courseware
from traittutor.generate.flashcards import validate_flashcard_payload
from traittutor.generate.grounding import StructuredBatchValidationError
from traittutor.generate.runner import LLMRunMetadata


def _metadata_question_lesson(question: str) -> dict[str, Any]:
    return {
        "title": "光合作用",
        "lesson_goal": "解释光能如何转化为化学能。",
        "sections": [
            {
                "section_title": "能量转化",
                "goal": "理解光合作用的能量变化。",
                "core_content": "植物把光能转化为化学能。",
                "checkpoint": {
                    "question": question,
                    "success_criteria": "说明能量转化关系。",
                    "feedback_if_confused": "回看光能与化学能的关系。",
                },
                "reflection_prompt": "用自己的话解释这种能量转化。",
                "references": ["chunk-1"],
                "external_claims": [],
            }
        ],
        "final_takeaways": ["光合作用包含能量转化。"],
        "next_step_guidance": ["比较反应物和产物。"],
    }


@pytest.mark.parametrize(
    "question",
    [
        "用户上传的文件名是什么？",
        "以下哪个是该文档的扩展名？",
        "What is the name of the uploaded file?",
        "Which page number contains the source chunk?",
    ],
)
def test_courseware_rejects_source_metadata_checkpoints(question: str) -> None:
    with pytest.raises(ValueError, match="source metadata"):
        _lesson_schema(_metadata_question_lesson(question))


def test_flashcard_rejects_source_metadata_recall_target() -> None:
    chunks = [
        {
            "source_id": "upload-1",
            "chunk_id": "chunk-1",
            "text": "植物把光能转化为化学能。",
        }
    ]
    payload = {
        "items": [
            {
                "node_id": "chunk-1",
                "node_name": "光合作用",
                "front": "用户上传的文件名是什么？",
                "back": "biology-notes.pdf",
                "references": [
                    {
                        "source_id": "upload-1",
                        "chunk_id": "chunk-1",
                        "text_snippet": "植物把光能转化为化学能。",
                    }
                ],
            }
        ]
    }

    with pytest.raises(StructuredBatchValidationError, match="source metadata"):
        validate_flashcard_payload(payload, chunks)


def test_quiz_and_flashcard_prompts_do_not_receive_material_title() -> None:
    variables = {
        "language": "zh-CN",
        "material_title": "不要出现在模型输入里的文件名.pdf",
        "learner_strategy_json": {},
        "generation_options_json": {},
        "batch_plan_json": {},
        "material_chunks_json": [
            {"source_id": "upload-1", "chunk_id": "chunk-1", "text": "正文知识。"}
        ],
    }

    for prompt_path in ("quiz/km-question-note.md", "flashcards/km-card-note.md"):
        prompt = load_prompt(prompt_path, variables)
        rendered = f"{prompt.system_prompt}\n{prompt.user_prompt}"
        assert variables["material_title"] not in rendered
        assert "Source-container metadata is provenance" in prompt.system_prompt


@pytest.mark.asyncio
async def test_courseware_model_input_strips_file_and_locator_metadata() -> None:
    captured: list[PromptDefinition] = []
    responses: list[Mapping[str, Any]] = [
        {
            "topic": "光合作用",
            "material_intent": "learn_new_topic",
            "material_model": {"subject": "生物"},
            "core_concepts": ["能量转化"],
            "prerequisite_relations": [],
            "difficulty_points": ["区分能量形式"],
            "adaptable_zones": [],
            "generation_mix": {"explanation": True},
        },
        {
            "lesson_structure": ["解释", "检查"],
            "scaffolding": "standard",
            "checkpoints": ["解释能量转化"],
            "feedback_if_confused": "对照两种能量形式。",
            "reflection": "复述转化过程。",
            "visible_teaching_moves": ["先解释后检查"],
            "selected_slr_actions": [],
            "intent_alignment": "new topic",
            "generation_mix": {"explanation": True},
        },
        _metadata_question_lesson("光合作用中发生了怎样的能量转化？"),
    ]

    async def run(
        prompt: PromptDefinition,
        *,
        validate: Any,
        **kwargs: Any,
    ) -> tuple[dict[str, Any], LLMRunMetadata]:
        captured.append(prompt)
        response = dict(responses[len(captured) - 1])
        validate(response)
        return response, LLMRunMetadata(
            model="test",
            provider="test",
            prompt_name=prompt.name,
            prompt_signature=prompt.signature,
            reasoning_effort=prompt.reasoning_effort,
        )

    await generate_courseware(
        chunks=[
            {
                "source_id": "upload-1",
                "chunk_id": "chunk-1",
                "title": "绝不能成为题目的文件名.pdf",
                "text": "植物把光能转化为化学能。",
                "citation": {
                    "locator": {
                        "filename": "绝不能成为题目的文件名.pdf",
                        "page": 7,
                        "path": "/private/upload/绝不能成为题目的文件名.pdf",
                    }
                },
            }
        ],
        learner_strategy={},
        run=run,
    )

    rendered = "\n".join(f"{prompt.system_prompt}\n{prompt.user_prompt}" for prompt in captured)
    assert "绝不能成为题目的文件名.pdf" not in rendered
    assert "/private/upload" not in rendered
    assert '"page": 7' not in rendered


@pytest.mark.asyncio
async def test_courseware_reuses_precomputed_analysis_without_llm() -> None:
    """A complete material analysis skips the content-analysis LLM stage."""
    captured: list[PromptDefinition] = []
    responses = [
        {
            "lesson_structure": ["解释", "检查"],
            "scaffolding": "standard",
            "checkpoints": ["解释能量转化"],
            "feedback_if_confused": "对照两种能量形式。",
            "reflection": "复述转化过程。",
            "visible_teaching_moves": ["先解释后检查"],
            "selected_slr_actions": [],
            "intent_alignment": "new topic",
            "generation_mix": {"explanation": True},
        },
        _metadata_question_lesson("光合作用"),
    ]

    async def run(
        prompt: PromptDefinition,
        *,
        validate: Any,
        **kwargs: Any,
    ) -> tuple[dict[str, Any], LLMRunMetadata]:
        captured.append(prompt)
        response = dict(responses[len(captured) - 1])
        validate(response)
        return response, LLMRunMetadata(
            model="test",
            provider="test",
            prompt_name=prompt.name,
            prompt_signature=prompt.signature,
            reasoning_effort=prompt.reasoning_effort,
        )

    await generate_courseware(
        chunks=[
            {
                "source_id": "upload-1",
                "chunk_id": "chunk-1",
                "text": "光合作用。植物把光能转化为化学能。",
            }
        ],
        learner_strategy={},
        precomputed_analysis={
            "topic": "光合作用",
            "material_intent": "learn_new_topic",
            "material_model": {"subject": "biology"},
            "core_concepts": [{"concept_id": "c1", "label": "光合作用"}],
            "prerequisite_relations": [],
            "difficulty_points": ["区分能量形式"],
            "adaptable_zones": [],
            "generation_mix": {"explanation": True},
            "reused_from_material_analysis": True,
        },
        run=run,
    )

    # Only adaptation-plan and lesson run; content-analysis is skipped.
    assert [prompt.name for prompt in captured] == [
        "traittutor-adaptation-plan",
        "traittutor-courseware",
    ]
    plan_prompt = captured[0]
    assert "reused_from_material_analysis" in plan_prompt.user_prompt


@pytest.mark.asyncio
async def test_courseware_goal_map_mode_uses_low_reasoning() -> None:
    """goal_map_mode runs the planning stages with a low reasoning tier (the
    demo provider proved unreliable under ``none``, so the human chose low)."""
    captured: list[PromptDefinition] = []
    seen_kwargs: list[dict[str, Any]] = []
    responses = [
        {
            "lesson_structure": ["解释", "检查"],
            "scaffolding": "standard",
            "checkpoints": ["解释能量转化"],
            "feedback_if_confused": "对照两种能量形式。",
            "reflection": "复述转化过程。",
            "visible_teaching_moves": ["先解释后检查"],
            "selected_slr_actions": [],
            "intent_alignment": "new topic",
            "generation_mix": {"explanation": True},
        },
        _metadata_question_lesson("光合作用"),
    ]

    async def run(
        prompt: PromptDefinition,
        *,
        validate: Any,
        **kwargs: Any,
    ) -> tuple[dict[str, Any], LLMRunMetadata]:
        captured.append(prompt)
        seen_kwargs.append(kwargs)
        response = dict(responses[len(captured) - 1])
        validate(response)
        return response, LLMRunMetadata(
            model="test",
            provider="test",
            prompt_name=prompt.name,
            prompt_signature=prompt.signature,
            reasoning_effort=prompt.reasoning_effort,
        )

    await generate_courseware(
        chunks=[{"source_id": "upload-1", "chunk_id": "chunk-1", "text": "光合作用。"}],
        learner_strategy={},
        precomputed_analysis={
            "topic": "光合作用",
            "material_intent": "learn_new_topic",
            "material_model": {"subject": "biology"},
            "core_concepts": [{"concept_id": "c1", "label": "光合作用"}],
            "prerequisite_relations": [],
            "difficulty_points": ["区分能量形式"],
            "adaptable_zones": [],
            "generation_mix": {"explanation": True},
            "reused_from_material_analysis": True,
        },
        goal_map_mode=True,
        run=run,
    )

    assert [prompt.name for prompt in captured] == [
        "traittutor-adaptation-plan",
        "traittutor-courseware",
    ]
    assert all(call.get("reasoning_effort") == "low" for call in seen_kwargs)


@pytest.mark.asyncio
async def test_courseware_goal_map_mode_bounds_output_tokens() -> None:
    """goal_map_mode caps lesson/plan output so slow providers finish inside
    the instruction executor budget (milestones come from the arrangement)."""
    captured: list[PromptDefinition] = []
    responses = [
        {
            "lesson_structure": ["解释", "检查"],
            "scaffolding": "standard",
            "checkpoints": ["解释能量转化"],
            "feedback_if_confused": "对照两种能量形式。",
            "reflection": "复述转化过程。",
            "visible_teaching_moves": ["先解释后检查"],
            "selected_slr_actions": [],
            "intent_alignment": "new topic",
            "generation_mix": {"explanation": True},
        },
        _metadata_question_lesson("光合作用"),
    ]

    async def run(
        prompt: PromptDefinition,
        *,
        validate: Any,
        **kwargs: Any,
    ) -> tuple[dict[str, Any], LLMRunMetadata]:
        captured.append(prompt)
        response = dict(responses[len(captured) - 1])
        validate(response)
        return response, LLMRunMetadata(
            model="test",
            provider="test",
            prompt_name=prompt.name,
            prompt_signature=prompt.signature,
            reasoning_effort=prompt.reasoning_effort,
        )

    await generate_courseware(
        chunks=[{"source_id": "upload-1", "chunk_id": "chunk-1", "text": "光合作用。"}],
        learner_strategy={},
        precomputed_analysis={
            "topic": "光合作用",
            "material_intent": "learn_new_topic",
            "material_model": {"subject": "biology"},
            "core_concepts": [{"concept_id": "c1", "label": "光合作用"}],
            "prerequisite_relations": [],
            "difficulty_points": ["区分能量形式"],
            "adaptable_zones": [],
            "generation_mix": {"explanation": True},
            "reused_from_material_analysis": True,
        },
        goal_map_mode=True,
        run=run,
    )

    plan_prompt, lesson_prompt = captured
    assert plan_prompt.max_output_tokens <= 2_000
    assert lesson_prompt.max_output_tokens <= 2_500
    assert "Demo mode, goal-map preview" in lesson_prompt.user_prompt


@pytest.mark.asyncio
async def test_courseware_demo_mode_bounds_output_tokens() -> None:
    """Non-goal-map components also cap output while demoing so slow providers
    stay inside the instruction executor budget."""
    captured: list[PromptDefinition] = []
    responses = [
        {
            "lesson_structure": ["解释", "检查"],
            "scaffolding": "standard",
            "checkpoints": ["解释能量转化"],
            "feedback_if_confused": "对照两种能量形式。",
            "reflection": "复述转化过程。",
            "visible_teaching_moves": ["先解释后检查"],
            "selected_slr_actions": [],
            "intent_alignment": "new topic",
            "generation_mix": {"explanation": True},
        },
        _metadata_question_lesson("光合作用"),
    ]

    async def run(
        prompt: PromptDefinition,
        *,
        validate: Any,
        **kwargs: Any,
    ) -> tuple[dict[str, Any], LLMRunMetadata]:
        captured.append(prompt)
        response = dict(responses[len(captured) - 1])
        validate(response)
        return response, LLMRunMetadata(
            model="test",
            provider="test",
            prompt_name=prompt.name,
            prompt_signature=prompt.signature,
            reasoning_effort=prompt.reasoning_effort,
        )

    await generate_courseware(
        chunks=[{"source_id": "upload-1", "chunk_id": "chunk-1", "text": "光合作用。"}],
        learner_strategy={},
        precomputed_analysis={
            "topic": "光合作用",
            "material_intent": "learn_new_topic",
            "material_model": {"subject": "biology"},
            "core_concepts": [{"concept_id": "c1", "label": "光合作用"}],
            "prerequisite_relations": [],
            "difficulty_points": ["区分能量形式"],
            "adaptable_zones": [],
            "generation_mix": {"explanation": True},
            "reused_from_material_analysis": True,
        },
        run=run,
    )

    plan_prompt, lesson_prompt = captured
    assert plan_prompt.max_output_tokens <= 3_000
    assert lesson_prompt.max_output_tokens <= 6_000
    assert "Demo mode: keep sections concise" in lesson_prompt.user_prompt


@pytest.mark.asyncio
async def test_courseware_non_goal_map_uses_demo_low_reasoning() -> None:
    """Other courseware components run a low reasoning tier while demoing."""
    captured: list[PromptDefinition] = []
    seen_kwargs: list[dict[str, Any]] = []
    responses = [
        {
            "lesson_structure": ["解释", "检查"],
            "scaffolding": "standard",
            "checkpoints": ["解释能量转化"],
            "feedback_if_confused": "对照两种能量形式。",
            "reflection": "复述转化过程。",
            "visible_teaching_moves": ["先解释后检查"],
            "selected_slr_actions": [],
            "intent_alignment": "new topic",
            "generation_mix": {"explanation": True},
        },
        _metadata_question_lesson("光合作用"),
    ]

    async def run(
        prompt: PromptDefinition,
        *,
        validate: Any,
        **kwargs: Any,
    ) -> tuple[dict[str, Any], LLMRunMetadata]:
        captured.append(prompt)
        seen_kwargs.append(kwargs)
        response = dict(responses[len(captured) - 1])
        validate(response)
        return response, LLMRunMetadata(
            model="test",
            provider="test",
            prompt_name=prompt.name,
            prompt_signature=prompt.signature,
            reasoning_effort=prompt.reasoning_effort,
        )

    await generate_courseware(
        chunks=[{"source_id": "upload-1", "chunk_id": "chunk-1", "text": "光合作用。"}],
        learner_strategy={},
        precomputed_analysis={
            "topic": "光合作用",
            "material_intent": "learn_new_topic",
            "material_model": {"subject": "biology"},
            "core_concepts": [{"concept_id": "c1", "label": "光合作用"}],
            "prerequisite_relations": [],
            "difficulty_points": ["区分能量形式"],
            "adaptable_zones": [],
            "generation_mix": {"explanation": True},
            "reused_from_material_analysis": True,
        },
        run=run,
    )

    assert [prompt.name for prompt in captured] == [
        "traittutor-adaptation-plan",
        "traittutor-courseware",
    ]
    assert all(call.get("reasoning_effort") == "low" for call in seen_kwargs)


@pytest.mark.asyncio
async def test_courseware_incomplete_analysis_falls_back_to_llm_stage() -> None:
    """An analysis missing the contract keys still runs the LLM analysis node."""
    captured: list[PromptDefinition] = []
    responses = [
        {
            "topic": "光合作用",
            "material_intent": "learn_new_topic",
            "material_model": {"subject": "生物"},
            "core_concepts": ["能量转化"],
            "prerequisite_relations": [],
            "difficulty_points": ["区分能量形式"],
            "adaptable_zones": [],
            "generation_mix": {"explanation": True},
        },
        {
            "lesson_structure": ["解释", "检查"],
            "scaffolding": "standard",
            "checkpoints": ["解释能量转化"],
            "feedback_if_confused": "对照两种能量形式。",
            "reflection": "复述转化过程。",
            "visible_teaching_moves": ["先解释后检查"],
            "selected_slr_actions": [],
            "intent_alignment": "new topic",
            "generation_mix": {"explanation": True},
        },
        _metadata_question_lesson("光合作用"),
    ]

    async def run(
        prompt: PromptDefinition,
        *,
        validate: Any,
        **kwargs: Any,
    ) -> tuple[dict[str, Any], LLMRunMetadata]:
        captured.append(prompt)
        response = dict(responses[len(captured) - 1])
        validate(response)
        return response, LLMRunMetadata(
            model="test",
            provider="test",
            prompt_name=prompt.name,
            prompt_signature=prompt.signature,
            reasoning_effort=prompt.reasoning_effort,
        )

    await generate_courseware(
        chunks=[{"source_id": "upload-1", "chunk_id": "chunk-1", "text": "光合作用。"}],
        learner_strategy={},
        precomputed_analysis={"subject": "biology", "concept_candidates": []},
        run=run,
    )

    # Missing topic/core_concepts/difficulty_points -> LLM analysis runs.
    assert [prompt.name for prompt in captured] == [
        "traittutor-content-analysis",
        "traittutor-adaptation-plan",
        "traittutor-courseware",
    ]
