from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from traittutor.generate.grounding import GroundingChunk, StructuredBatchValidationError
from traittutor.generate.quiz import plan_quiz_batches, validate_quiz_batch

PROMPT = (
    Path(__file__).resolve().parents[2]
    / "traittutor/generate/prompts/quiz/km-question-note.yml"
)


def _chunks() -> tuple[GroundingChunk, ...]:
    return (
        GroundingChunk(
            source_id="knowledge:sem",
            chunk_id="sem-001",
            text="关键词将搜索意图与相关内容连接起来。",
        ),
        GroundingChunk(
            source_id="notebook:campaign",
            chunk_id="campaign-001",
            text="转化路径应从目标受众识别开始，再选择匹配内容。",
        ),
        GroundingChunk(
            source_id="upload:brief",
            chunk_id="brief-001",
            text="出价决定广告参与竞价时愿意支付的最高金额。",
        ),
    )


def test_quiz_prompt_is_strict_source_grounded_and_high_reasoning():
    prompt = yaml.safe_load(PROMPT.read_text(encoding="utf-8"))
    schema = json.loads(prompt["json_schema"])
    item_schema = schema["properties"]["items"]["items"]
    reference_schema = item_schema["properties"]["references"]["items"]
    instructions = "\n".join(part["prompt"] for part in prompt["prompt_structure"])

    assert prompt["reasoning_effort"] == "high"
    assert schema["additionalProperties"] is False
    assert "correct_answer" in item_schema["required"]
    assert {"source_id", "chunk_id", "text_snippet"} <= set(reference_schema["required"])
    assert "answerable" in instructions
    assert "personality scores" in instructions


def test_quiz_batch_plan_tracks_source_order_and_question_id_ranges():
    plans = plan_quiz_batches(
        _chunks(),
        chunks_per_batch=2,
        questions_per_batch=3,
        first_question_id=10,
    )

    assert [plan.batch_index for plan in plans] == [1, 2]
    assert plans[0].chunk_ids == ("sem-001", "campaign-001")
    assert plans[0].question_id_start == 10
    assert plans[1].question_id_start == 13
    assert all(plan.question_count == 3 for plan in plans)


def test_quiz_batch_is_displayable_only_after_full_answerability_and_grounding_validation():
    raw = json.dumps(
        {
            "items": [
                {
                    "node_id": "campaign-001",
                    "node_name": "转化路径",
                    "question_id": 1,
                    "question": "转化路径的第一步是什么？",
                    "question_type": "OPTIONS",
                    "difficulty": "easy",
                    "options": [
                        {"text": "识别目标受众", "is_correct": True},
                        {"text": "提高出价", "is_correct": False},
                        {"text": "更换广告颜色", "is_correct": False},
                        {"text": "删除匹配内容", "is_correct": False},
                    ],
                    "correct_answer": "识别目标受众",
                    "explanation": "识别目标受众。材料说明转化路径应从目标受众识别开始。",
                    "references": [
                        {
                            "source_id": "notebook:campaign",
                            "chunk_id": "campaign-001",
                            "text_snippet": "转化路径应从目标受众识别开始，再选择匹配内容。",
                        }
                    ],
                },
                {
                    "node_id": "sem-001",
                    "node_name": "关键词",
                    "question_id": 2,
                    "question": "关键词连接哪两类内容？",
                    "question_type": "SHORT_ANSWER",
                    "difficulty": "easy",
                    "options": [],
                    "correct_answer": "搜索意图与相关内容",
                    "explanation": "搜索意图与相关内容。材料直接说明关键词将两者连接起来。",
                    "references": [
                        {
                            "source_id": "knowledge:sem",
                            "chunk_id": "sem-001",
                            "text_snippet": "关键词将搜索意图与相关内容连接起来。",
                        }
                    ],
                },
            ]
        }
    )

    batch = validate_quiz_batch(raw, iter(_chunks()))

    assert batch.displayable is True
    assert batch.items[0].correct_answer == "识别目标受众"


def test_quiz_batch_rejects_partial_json_and_unanswerable_option_setup():
    with pytest.raises(StructuredBatchValidationError) as partial_error:
        validate_quiz_batch('{"items": [', _chunks())

    assert partial_error.value.displayable is False

    raw = {
        "items": [
            {
                "node_id": "campaign-001",
                "node_name": "转化路径",
                "question_id": 1,
                "question": "转化路径的第一步是什么？",
                "question_type": "OPTIONS",
                "difficulty": "easy",
                "options": [
                    {"text": "识别目标受众", "is_correct": True},
                    {"text": "提高出价", "is_correct": True},
                    {"text": "更换广告颜色", "is_correct": False},
                    {"text": "删除匹配内容", "is_correct": False},
                ],
                "correct_answer": "识别目标受众",
                "explanation": "识别目标受众。材料说明转化路径应从目标受众识别开始。",
                "references": [
                    {
                        "source_id": "notebook:campaign",
                        "chunk_id": "campaign-001",
                        "text_snippet": "转化路径应从目标受众识别开始，再选择匹配内容。",
                    }
                ],
            }
        ]
    }

    with pytest.raises(StructuredBatchValidationError) as answerability_error:
        validate_quiz_batch(raw, _chunks())

    assert answerability_error.value.displayable is False

    raw["items"][0]["options"][1]["is_correct"] = False
    raw["items"][0]["question_id"] = "1"

    with pytest.raises(StructuredBatchValidationError) as schema_error:
        validate_quiz_batch(raw, _chunks())

    assert schema_error.value.displayable is False
