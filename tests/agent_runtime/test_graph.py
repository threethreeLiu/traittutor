from __future__ import annotations

import pytest

from traittutor.agent_runtime.graph import run_agent
from traittutor.agent_runtime.schemas import AgentMode, AgentRunRequest, Intent


@pytest.mark.asyncio
async def test_learn_mode_routes_to_learning_coach(monkeypatch):
    class FakeResponse:
        content = "Let's learn."
        request_id = "gateway-1"

    class FakeGateway:
        async def complete(self, request):
            assert request.purpose == "agent:learning_coach"
            assert request.reasoning_effort == "high"
            assert "请严格使用中文（简体）" in request.system_prompt
            return FakeResponse()

    monkeypatch.setattr("traittutor.agent_runtime.graph.get_gateway", lambda: FakeGateway())
    result = await run_agent(AgentRunRequest(mode=AgentMode.LEARN, message="Help me study calculus", language="zh"))
    assert result.intent is Intent.LEARNING
    assert result.agent == "learning_coach"
    assert result.content == "Let's learn."


@pytest.mark.asyncio
async def test_assist_routes_writing_and_policy(monkeypatch):
    class FakeResponse:
        content = "Draft"
        request_id = "gateway-2"

    class FakeGateway:
        async def complete(self, request):
            return FakeResponse()

    monkeypatch.setattr("traittutor.agent_runtime.graph.get_gateway", lambda: FakeGateway())
    result = await run_agent(AgentRunRequest(mode=AgentMode.ASSIST, message="Write a study plan"))
    assert result.intent is Intent.WRITING
    assert result.agent == "task_agent"
    assert result.policy[0].decision == "allowed"
