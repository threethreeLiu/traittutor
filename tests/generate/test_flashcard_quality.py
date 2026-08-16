from __future__ import annotations

import pytest

from traittutor.generate.flashcards import validate_flashcard_payload
from traittutor.generate.grounding import StructuredBatchValidationError


def _payload(front: str) -> dict[str, object]:
    return {
        "items": [
            {
                "node_id": "goal-1",
                "node_name": "质能方程",
                "front": front,
                "back": "质量与能量之间的等价关系。",
                "references": [
                    {
                        "source_id": "goal",
                        "chunk_id": "goal-1",
                        "text_snippet": "我想学习爱因斯坦质能方程",
                    }
                ],
            }
        ]
    }


def test_flashcards_repair_a_reference_quote_with_server_owned_chunk_text() -> None:
    chunks = [
        {
            "source_id": "goal",
            "chunk_id": "goal-1",
            "text": "我想学习爱因斯坦质能方程",
        }
    ]
    payload = _payload("质能方程描述了质量与能量之间的什么关系？")
    payload["items"][0]["references"][0]["text_snippet"] = "材料中不存在的引用"

    validated = validate_flashcard_payload(payload, chunks)

    assert validated.items[0].references[0].text_snippet == chunks[0]["text"]


def test_flashcards_repair_truncated_source_id_and_missing_reference_fields() -> None:
    chunks = [
        {
            "source_id": "paste-a20b64d6d2bb952527ae",
            "chunk_id": "material-b3103c8c87dc4abb1be2d50e",
            "text": "复杂度关心的是随 n 增长的趋势，而不是某一次运行的确切秒数。",
        }
    ]
    payload = _payload("复杂度关心的是什么？")
    payload["items"][0]["node_id"] = chunks[0]["chunk_id"]
    payload["items"][0]["references"] = [{"source_id": "paste-a20b64d6d2"}]

    validated = validate_flashcard_payload(payload, chunks)
    reference = validated.items[0].references[0]

    assert reference.source_id == chunks[0]["source_id"]
    assert reference.chunk_id == chunks[0]["chunk_id"]
    assert reference.text_snippet == chunks[0]["text"]


def test_flashcards_do_not_repair_an_ambiguous_or_unknown_source() -> None:
    chunks = [
        {"source_id": "source-a", "chunk_id": "chunk-a", "text": "材料 A"},
        {"source_id": "source-b", "chunk_id": "chunk-b", "text": "材料 B"},
    ]
    payload = _payload("这道题考察什么知识？")
    payload["items"][0]["node_id"] = "unknown"
    payload["items"][0]["references"] = [{"source_id": "unknown"}]

    with pytest.raises(StructuredBatchValidationError, match="Field required"):
        validate_flashcard_payload(payload, chunks)
