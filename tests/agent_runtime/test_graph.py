from __future__ import annotations

import pytest

from traittutor import learning_packs
from traittutor.agent_runtime.graph import run_agent
from traittutor.agent_runtime.schemas import AgentMode, AgentRunRequest, Intent


@pytest.fixture(autouse=True)
def isolated_learning_plan(tmp_path, monkeypatch):
    monkeypatch.setattr(learning_packs, "_path", lambda: tmp_path / "learning-packs.json")

    class Planned:
        def __init__(self, pack_id: str):
            self.pack_id = pack_id

        def model_dump(self):
            return {
                "plan_id": f"plan-{self.pack_id}",
                "pack_id": self.pack_id,
                "version": 1,
                "goal": "Learn",
                "subject_ref": {"subject_id": "general", "label": "General"},
                "components": [{"component_id": "cmp-1", "component_type": "diagnostic_check"}],
                "status": "active",
                "created_at": "2026-08-03T00:00:00+00:00",
                "updated_at": "2026-08-03T00:00:00+00:00",
            }

    monkeypatch.setattr(
        "traittutor.agent_runtime.graph.build_learning_component_plan",
        lambda pack, instruction="": Planned(str(pack["pack_id"])),
    )


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
            assert "learning launch" in request.system_prompt
            assert "authoritative structured component path" in request.system_prompt
            assert "Do not invent a second path" in request.system_prompt
            return FakeResponse()

    monkeypatch.setattr("traittutor.agent_runtime.graph.get_gateway", lambda: FakeGateway())
    result = await run_agent(AgentRunRequest(mode=AgentMode.LEARN, message="Help me study calculus", language="zh"))
    assert result.intent is Intent.LEARNING
    assert result.agent == "learning_coach"
    assert result.content == "Let's learn."
    assert result.product_action is not None
    assert result.product_action["type"] == "learning_plan_created"
    assert result.product_action["start_url"].startswith("/space/learning/")


@pytest.mark.asyncio
async def test_learning_coach_reads_uploaded_material_and_requires_visible_feedback(monkeypatch):
    class FakeResponse:
        content = "已收到英文材料。"
        request_id = "gateway-material"

    class FakeGateway:
        async def complete(self, request):
            assert "<learning_sources>" in request.prompt
            assert "compound interest" in request.prompt
            assert "Acknowledge receipt in the first sentence" in request.system_prompt
            assert "source-grounded concepts" in request.system_prompt
            assert "untrusted study data" in request.system_prompt
            return FakeResponse()

    monkeypatch.setattr("traittutor.agent_runtime.graph.get_gateway", lambda: FakeGateway())
    result = await run_agent(AgentRunRequest(
        mode=AgentMode.LEARN,
        message="请分析我上传的英文 PDF",
        materials=["Personal finance lesson: compound interest and risk diversification."],
        language="zh",
    ))
    assert result.intent is Intent.LEARNING
    assert result.content == "已收到英文材料。"


@pytest.mark.asyncio
async def test_capability_question_routes_to_product_guide_even_in_learn_mode(monkeypatch):
    class FakeResponse:
        content = "我可以把目标变成学习路径。"
        request_id = "gateway-guide"

    class FakeGateway:
        async def complete(self, request):
            assert request.purpose == "agent:product_guide"
            assert "structured lesson, flashcards and a Quiz" in request.system_prompt
            return FakeResponse()

    monkeypatch.setattr("traittutor.agent_runtime.graph.get_gateway", lambda: FakeGateway())
    result = await run_agent(AgentRunRequest(mode=AgentMode.LEARN, message="你能干嘛？", language="zh"))
    assert result.intent is Intent.GENERAL
    assert result.agent == "product_guide"


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
