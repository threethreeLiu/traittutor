"""Tests for BaseAgent runtime binding behavior."""

from __future__ import annotations

from traittutor.agents.base_agent import BaseAgent
from traittutor.services.llm.config import LLMConfig


class _DummyAgent(BaseAgent):
    async def process(self, **_kwargs):  # noqa: ANN003
        return {}


def test_base_agent_defaults_to_resolved_binding(monkeypatch) -> None:
    """When binding is not explicitly provided, use resolved runtime binding."""
    resolved = LLMConfig(
        model="google/gemini-3-flash-preview",
        api_key="sk-test",
        base_url="https://openrouter.ai/api/v1",
        binding="openrouter",
        provider_name="openrouter",
        provider_mode="gateway",
    )
    monkeypatch.setattr("traittutor.agents.base_agent.get_llm_config", lambda: resolved)

    agent = _DummyAgent(
        module_name="question",
        agent_name="idea_agent",
        language="en",
    )

    assert agent.binding == "openrouter"
