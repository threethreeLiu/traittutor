from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from traittutor.generate.flashcards import (
    plan_flashcard_batches,
    validate_flashcard_batch,
)
from traittutor.generate.grounding import GroundingChunk, StructuredBatchValidationError

PROMPT = (
    Path(__file__).resolve().parents[2]
    / "traittutor/generate/prompts/flashcards/km-card-note.yml"
)


def _chunks() -> tuple[GroundingChunk, ...]:
    return (
        GroundingChunk(
            source_id="knowledge:sem",
            chunk_id="sem-001",
            text="关键词将搜索意图与相关内容连接起来。",
        ),
        GroundingChunk(
            source_id="knowledge:sem",
            chunk_id="sem-002",
            text="出价决定广告参与竞价时愿意支付的最高金额。",
        ),
        GroundingChunk(
            source_id="notebook:campaign",
            chunk_id="campaign-001",
            text="转化路径应从目标受众识别开始，再选择匹配内容。",
        ),
    )


def test_flashcard_prompt_is_strict_source_grounded_and_high_reasoning():
    prompt = yaml.safe_load(PROMPT.read_text(encoding="utf-8"))
    schema = json.loads(prompt["json_schema"])
    item_schema = schema["properties"]["items"]["items"]
    reference_schema = item_schema["properties"]["references"]["items"]
    instructions = "\n".join(part["prompt"] for part in prompt["prompt_structure"])

    assert prompt["reasoning_effort"] == "high"
    assert schema["additionalProperties"] is False
    assert item_schema["additionalProperties"] is False
    assert {"source_id", "chunk_id", "text_snippet"} <= set(reference_schema["required"])
    assert "strict JSON" in instructions
    assert "atomic" in instructions
    assert "personality scores" in instructions


def test_flashcard_batch_plan_preserves_chunk_order_and_small_batch_limit():
    plans = plan_flashcard_batches(_chunks(), chunks_per_batch=2, cards_per_batch=3)

    assert [plan.batch_index for plan in plans] == [1, 2]
    assert [plan.total_batches for plan in plans] == [2, 2]
    assert plans[0].chunk_ids == ("sem-001", "sem-002")
    assert plans[1].chunk_ids == ("campaign-001",)
    assert all(plan.item_limit == 3 for plan in plans)


def test_flashcard_batch_is_displayable_only_after_complete_grounded_json_validation():
    raw = json.dumps(
        {
            "items": [
                {
                    "node_id": "sem-001",
                    "node_name": "关键词",
                    "front": "关键词连接什么？",
                    "back": "它连接搜索意图与相关内容。",
                    "references": [
                        {
                            "source_id": "knowledge:sem",
                            "chunk_id": "sem-001",
                            "text_snippet": "关键词将搜索意图与相关内容连接起来。",
                        }
                    ],
                }
            ]
        }
    )

    batch = validate_flashcard_batch(raw, _chunks())

    assert batch.displayable is True
    assert batch.items[0].references[0].chunk_id == "sem-001"


def test_flashcard_batch_rejects_partial_json_and_non_atomic_cards():
    with pytest.raises(StructuredBatchValidationError) as partial_error:
        validate_flashcard_batch('{"items": [', _chunks())

    assert partial_error.value.displayable is False

    raw = {
        "items": [
            {
                "node_id": "sem-001",
                "node_name": "关键词与出价",
                "front": "关键词连接什么？出价决定什么？",
                "back": "关键词连接搜索意图与内容；出价决定最高支付金额。",
                "references": [
                    {
                        "source_id": "knowledge:sem",
                        "chunk_id": "sem-001",
                        "text_snippet": "关键词将搜索意图与相关内容连接起来。",
                    }
                ],
            }
        ]
    }

    with pytest.raises(StructuredBatchValidationError) as atomicity_error:
        validate_flashcard_batch(raw, _chunks())

    assert atomicity_error.value.displayable is False


def test_flashcard_batch_rejects_reference_quotes_not_found_in_the_source_chunk():
    raw = {
        "items": [
            {
                "node_id": "sem-001",
                "node_name": "关键词",
                "front": "关键词连接什么？",
                "back": "它连接搜索意图与相关内容。",
                "references": [
                    {
                        "source_id": "knowledge:sem",
                        "chunk_id": "sem-001",
                        "text_snippet": "关键词决定最高支付金额。",
                    }
                ],
            }
        ]
    }

    with pytest.raises(StructuredBatchValidationError) as source_error:
        validate_flashcard_batch(raw, _chunks())

    assert source_error.value.displayable is False
