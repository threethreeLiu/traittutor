from __future__ import annotations

from typing import Any

import pytest

from traittutor.agents.chat import capability as chat_capability
from traittutor.capabilities.chat_modes import (
    HumanizerCapability,
    KnowledgeDiagramCapability,
    LearningExplorationCapability,
)
from traittutor.core.context import UnifiedContext


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("capability_type", "block_name", "prompt_fragment"),
    [
        (LearningExplorationCapability, "learning_exploration", "学习探索模式"),
        (KnowledgeDiagramCapability, "knowledge_diagram", "知识图解模式"),
        (HumanizerCapability, "humanizer", "自然改写模式"),
    ],
)
async def test_mode_prompt_is_server_owned_and_user_message_stays_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    capability_type: type[Any],
    block_name: str,
    prompt_fragment: str,
) -> None:
    observed: list[UnifiedContext] = []

    class _Pipeline:
        def __init__(self, *, language: str) -> None:
            assert language == "zh-CN"

        async def run(self, context: UnifiedContext, _stream: object) -> None:
            observed.append(context)

    monkeypatch.setattr(chat_capability, "AgenticChatPipeline", _Pipeline)
    context = UnifiedContext(user_message="只发送这一句用户问题", language="zh-CN")

    await capability_type().run(context, object())

    assert observed == [context]
    assert context.user_message == "只发送这一句用户问题"
    blocks = context.metadata["_extra_capability_blocks"]
    mode_block = next(block for block in blocks if block.name == block_name)
    assert prompt_fragment in mode_block.content
    assert all("TRAITTUTOR_" not in block.content for block in blocks)
