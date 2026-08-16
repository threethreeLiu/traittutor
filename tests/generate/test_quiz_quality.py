from __future__ import annotations

import pytest

from traittutor.generate.grounding import StructuredBatchValidationError
from traittutor.generate.quiz import validate_quiz_payload


def _payload(question: str) -> dict[str, object]:
    return {
        "items": [
            {
                "node_id": "goal-1",
                "node_name": "质能方程",
                "question_id": 1,
                "question": question,
                "question_type": "TF",
                "difficulty": "easy",
                "options": [
                    {"text": "正确", "is_correct": True},
                    {"text": "错误", "is_correct": False},
                ],
                "correct_answer": "正确",
                "explanation": "正确。给定文本明确表达了这一目标。",
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


def test_quiz_rejects_meta_question_about_the_user_goal() -> None:
    chunks = [
        {
            "source_id": "goal",
            "chunk_id": "goal-1",
            "text": "我想学习爱因斯坦质能方程",
        }
    ]

    with pytest.raises(StructuredBatchValidationError, match="user's request"):
        validate_quiz_payload(
            _payload("用户在本段文本中表达的学习目标与爱因斯坦质能方程相关。"),
            chunks,
        )


@pytest.mark.parametrize(
    "question",
    [
        "用户上传的文件名是什么？",
        "以下哪个是该文档的扩展名？",
        "What is the name of the uploaded file?",
        "Which page number contains the source chunk?",
    ],
)
def test_quiz_rejects_questions_about_source_metadata(question: str) -> None:
    chunks = [
        {
            "source_id": "goal",
            "chunk_id": "goal-1",
            "text": "我想学习爱因斯坦质能方程",
        }
    ]

    with pytest.raises(StructuredBatchValidationError, match="source metadata"):
        validate_quiz_payload(_payload(question), chunks)


def test_quiz_keeps_subject_level_true_false_questions() -> None:
    chunks = [
        {
            "source_id": "goal",
            "chunk_id": "goal-1",
            "text": "我想学习爱因斯坦质能方程",
        }
    ]
    payload = _payload("质能方程描述了质量与能量之间的等价关系。")

    assert validate_quiz_payload(payload, chunks).items[0].question.startswith("质能方程")


@pytest.mark.parametrize(
    "question",
    [
        "PNG 文件格式支持透明通道。",
        "该文件格式如何压缩数据？",
        "该文件格式为什么能够保留透明通道？",
        "A source file can be compiled into an object file.",
        "How is a virtual-memory page number translated into a physical frame?",
    ],
)
def test_quiz_keeps_subject_questions_that_use_file_terms(question: str) -> None:
    chunks = [
        {
            "source_id": "computing",
            "chunk_id": "computing-1",
            "text": question,
        }
    ]
    payload = _payload(question)
    payload["items"][0]["node_id"] = "computing-1"
    payload["items"][0]["references"] = [
        {
            "source_id": "computing",
            "chunk_id": "computing-1",
            "text_snippet": question,
        }
    ]

    assert validate_quiz_payload(payload, chunks).items[0].question == question


def test_quiz_does_not_reject_an_explanation_only_for_answer_prefix_format() -> None:
    chunks = [
        {
            "source_id": "goal",
            "chunk_id": "goal-1",
            "text": "我想学习爱因斯坦质能方程",
        }
    ]
    payload = _payload("质能方程描述了质量与能量之间的等价关系。")
    payload["items"][0]["explanation"] = "给定材料支持这个判断，答案是正确。"

    validated = validate_quiz_payload(payload, chunks)

    assert validated.items[0].explanation == "给定材料支持这个判断，答案是正确。"


def test_quiz_repairs_a_reference_quote_with_server_owned_chunk_text() -> None:
    chunks = [
        {
            "source_id": "goal",
            "chunk_id": "goal-1",
            "text": "我想学习爱因斯坦质能方程",
        }
    ]
    payload = _payload("质能方程描述了质量与能量之间的等价关系。")
    payload["items"][0]["references"][0]["text_snippet"] = "材料中不存在的引用"

    validated = validate_quiz_payload(payload, chunks)

    assert validated.items[0].references[0].text_snippet == chunks[0]["text"]


def test_quiz_repairs_truncated_source_id_and_missing_reference_fields() -> None:
    chunks = [
        {
            "source_id": "paste-a20b64d6d2bb952527ae",
            "chunk_id": "material-b3103c8c87dc4abb1be2d50e",
            "text": "复杂度关心的是随 n 增长的趋势，而不是某一次运行的确切秒数。",
        }
    ]
    payload = _payload("复杂度关心的是随输入规模增长的趋势。")
    payload["items"][0]["node_id"] = chunks[0]["chunk_id"]
    payload["items"][0]["references"] = [{"source_id": "paste-a20b64d6d2"}]

    validated = validate_quiz_payload(payload, chunks)
    reference = validated.items[0].references[0]

    assert reference.source_id == chunks[0]["source_id"]
    assert reference.chunk_id == chunks[0]["chunk_id"]
    assert reference.text_snippet == chunks[0]["text"]


def test_quiz_does_not_repair_an_ambiguous_or_unknown_source() -> None:
    chunks = [
        {"source_id": "source-a", "chunk_id": "chunk-a", "text": "材料 A"},
        {"source_id": "source-b", "chunk_id": "chunk-b", "text": "材料 B"},
    ]
    payload = _payload("这是一道知识题。")
    payload["items"][0]["node_id"] = "unknown"
    payload["items"][0]["references"] = [{"source_id": "unknown"}]

    with pytest.raises(StructuredBatchValidationError, match="Field required"):
        validate_quiz_payload(payload, chunks)
