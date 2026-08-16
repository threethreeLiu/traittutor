from __future__ import annotations

import pytest

from traittutor.generate.podcasts import generate_podcast_narration
from traittutor.generate.runner import LLMRunMetadata

LESSON = {
    "title": "矩阵变换",
    "lesson_goal": "理解矩阵如何表示线性变换",
    "sections": [
        {
            "section_title": "核心概念",
            "core_content": "矩阵可以表示对向量的线性变换。",
            "reflection_prompt": "这个变换如何改变方向？",
        }
    ],
    "final_takeaways": ["先观察基向量如何变化。"],
}


def _dialogue_payload() -> dict:
    return {
        "title": "矩阵播客",
        "dialogue": [
            {"speaker": "host", "text": "欢迎。今天我们根据课件理解矩阵变换。"},
            {"speaker": "guest", "text": "矩阵到底是怎么表示变换的？"},
            {"speaker": "host", "text": "矩阵可以表示对向量的线性变换。"},
            {"speaker": "guest", "text": "那基向量在这个过程中起什么作用？"},
            {"speaker": "host", "text": "先观察基向量如何变化，就能理解整个变换。"},
            {"speaker": "guest", "text": "明白了，谢谢讲解！"},
        ],
    }


@pytest.mark.asyncio
async def test_podcast_prompt_uses_lesson_language_and_source_boundary() -> None:
    captured: dict[str, str] = {}

    async def run(prompt, *, validate):
        captured["system"] = prompt.system_prompt
        captured["user"] = prompt.user_prompt
        payload = _dialogue_payload()
        validate(payload)
        return payload, LLMRunMetadata(
            model="test",
            provider="test",
            prompt_name=prompt.name,
            prompt_signature=prompt.signature,
            reasoning_effort="medium",
        )

    result = await generate_podcast_narration(lesson=LESSON, language="zh-CN", run=run)

    assert result["status"] == "completed"
    assert result["title"] == "矩阵播客"
    assert isinstance(result["dialogue"], list)
    assert len(result["dialogue"]) >= 4
    assert all(turn["speaker"] in {"host", "guest"} for turn in result["dialogue"])
    # script is a flattened display string derived from the dialogue
    assert "host:" in result["script"]
    assert "欢迎" in result["script"]
    assert "LANGUAGE HINT: zh-CN" in captured["user"]
    assert "Use only facts already present" in captured["system"]
    assert "矩阵可以表示对向量的线性变换" in captured["user"]


@pytest.mark.asyncio
async def test_podcast_generation_degrades_to_validated_lesson_text() -> None:
    async def fail(*_args, **_kwargs):
        raise RuntimeError("provider unavailable")

    result = await generate_podcast_narration(lesson=LESSON, language="zh-CN", run=fail)

    assert result["status"] == "degraded"
    # degraded dialogue is a single-host fallback turn
    assert isinstance(result["dialogue"], list)
    assert len(result["dialogue"]) == 1
    assert result["dialogue"][0]["speaker"] == "host"
    assert "矩阵可以表示对向量的线性变换" in result["dialogue"][0]["text"]
    assert "provider unavailable" in result["message"]


def test_podcast_validate_rejects_invalid_speaker() -> None:
    from traittutor.generate.podcasts import _validate

    bad_payload = {
        "title": "bad",
        "dialogue": [
            {"speaker": "narrator", "text": "oops"},
            {"speaker": "host", "text": "ok"},
            {"speaker": "guest", "text": "ok"},
            {"speaker": "host", "text": "ok"},
        ],
    }
    with pytest.raises(ValueError, match="speaker"):
        _validate(bad_payload)


def test_podcast_validate_rejects_too_few_turns() -> None:
    from traittutor.generate.podcasts import _validate

    bad_payload = {
        "title": "too short",
        "dialogue": [
            {"speaker": "host", "text": "only one turn"},
        ],
    }
    with pytest.raises(ValueError, match="turns"):
        _validate(bad_payload)
