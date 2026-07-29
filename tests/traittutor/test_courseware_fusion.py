from __future__ import annotations

import pytest

from traittutor.generate.courseware import generate_courseware
from traittutor.generate.runner import LLMRunMetadata


@pytest.mark.asyncio
async def test_courseware_uses_three_source_grounded_stages():
    responses = [
        {"topic": "光合作用", "core_concepts": ["转化"], "difficulty_points": ["能量路径"]},
        {"lesson_structure": "guided", "scaffolding": "high", "checkpoints": ["one"], "visible_teaching_moves": ["worked step"]},
        {"title": "光合作用", "lesson_goal": "理解转化", "sections": [{"section_title": "核心", "goal": "理解", "core_content": "内容", "checkpoint": {"question": "？", "success_criteria": "能解释", "feedback_if_confused": "回看材料"}, "reflection_prompt": "总结", "references": [{"chunk_id": "c1"}]}], "final_takeaways": ["要点"], "next_step_guidance": ["继续练习"]},
    ]
    seen = []

    async def run(prompt, *, validate):
        seen.append(prompt.name)
        value = responses.pop(0)
        validate(value)
        return value, LLMRunMetadata("m", "p", prompt.name, prompt.signature, "high")

    artifact = await generate_courseware(
        chunks=[{"chunk_id": "c1", "text": "光合作用把光能转为化学能。"}],
        learner_strategy={"checkpoint_frequency": "high"},
        run=run,
    )

    assert seen == ["traittutor-content-analysis", "traittutor-adaptation-plan", "traittutor-courseware"]
    assert artifact.lesson["sections"][0]["references"][0]["chunk_id"] == "c1"
