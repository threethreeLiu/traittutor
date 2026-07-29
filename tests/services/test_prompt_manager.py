"""Prompt manager path resolution tests."""

from __future__ import annotations

from traittutor.services.prompt import get_prompt_manager


def test_prompt_manager_loads_prompts_from_traittutor_tree() -> None:
    manager = get_prompt_manager()
    manager.clear_cache()

    prompts = manager.load_prompts(
        module_name="question",
        agent_name="idea_agent",
        language="en",
    )

    assert "generate_ideas" in prompts
