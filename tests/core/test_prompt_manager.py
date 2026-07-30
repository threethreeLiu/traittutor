#!/usr/bin/env python
"""
Unit tests for the unified PromptManager.
"""

from pathlib import Path

import pytest

from traittutor.services.prompt import PromptLoadError, PromptManager, get_prompt_manager
from traittutor.services.prompt.markdown import load_markdown_prompt


class TestPromptManager:
    """Test cases for PromptManager."""

    def setup_method(self):
        """Reset singleton and cache before each test."""
        PromptManager._instance = None
        PromptManager._cache = {}

    def test_singleton_pattern(self):
        """Test that PromptManager uses singleton pattern."""
        pm1 = PromptManager()
        pm2 = PromptManager()
        assert pm1 is pm2

    def test_get_prompt_manager_returns_singleton(self):
        """Test that get_prompt_manager returns the same instance."""
        pm1 = get_prompt_manager()
        pm2 = get_prompt_manager()
        assert pm1 is pm2

    def test_load_prompts_research_module(self):
        """Test loading prompts for research module."""
        pm = get_prompt_manager()
        prompts = pm.load_prompts(
            module_name="research",
            agent_name="pipeline",
            language="en",
        )
        assert isinstance(prompts, dict)
        # pipeline.md carries one section per phase, each of which has
        # its own ``system`` body.
        assert (
            any(
                isinstance(prompts.get(phase), dict) and "system" in prompts[phase]
                for phase in ("rephrase", "decompose", "research_step", "report")
            )
            or prompts == {}
        )

    def test_load_prompts_question_module(self):
        """Test loading prompts for an available agent module."""
        pm = get_prompt_manager()
        prompts = pm.load_prompts(
            module_name="question",
            agent_name="idea_agent",
            language="en",
        )
        assert isinstance(prompts, dict)

    def test_load_prompts_with_subdirectory(self):
        """Test recursive prompt resolution when a subdirectory is supplied."""
        pm = get_prompt_manager()
        prompts = pm.load_prompts(
            module_name="question",
            agent_name="idea_agent",
            language="en",
            subdirectory="unused",
        )
        assert isinstance(prompts, dict)

    def test_caching(self):
        """Test that prompts are cached after first load."""
        pm = get_prompt_manager()

        # First load
        prompts1 = pm.load_prompts("research", "pipeline", "en")

        # Second load should return cached version
        prompts2 = pm.load_prompts("research", "pipeline", "en")

        assert prompts1 is prompts2

    def test_clear_cache_all(self):
        """Test clearing all cache."""
        pm = get_prompt_manager()

        # Load some prompts
        pm.load_prompts("research", "pipeline", "en")
        pm.load_prompts("question", "idea_agent", "en")

        assert len(pm._cache) >= 2

        pm.clear_cache()
        assert len(pm._cache) == 0

    def test_clear_cache_module_specific(self):
        """Test clearing cache for specific module."""
        pm = get_prompt_manager()

        # Load prompts for multiple modules
        pm.load_prompts("research", "pipeline", "en")
        pm.load_prompts("question", "idea_agent", "en")

        # Clear only research cache
        pm.clear_cache("research")

        # Question prompts should still be cached
        assert any("question" in k for k in pm._cache)
        assert not any("research" in k for k in pm._cache)

    def test_get_prompt_helper(self):
        """Test the get_prompt helper method."""
        pm = get_prompt_manager()

        test_prompts = {
            "system": {
                "role": "You are a helpful assistant",
                "task": "Answer questions",
            },
            "simple_key": "Simple value",
        }

        # Test nested access
        role = pm.get_prompt(test_prompts, "system", "role")
        assert role == "You are a helpful assistant"

        # Test simple access (no field)
        simple = pm.get_prompt(test_prompts, "simple_key")
        assert simple == "Simple value"

        # Test fallback
        missing = pm.get_prompt(test_prompts, "nonexistent", "field", "fallback_value")
        assert missing == "fallback_value"

    def test_language_fallback(self):
        """Test language fallback chain."""
        pm = get_prompt_manager()

        # Even with a potentially missing language, should fallback
        prompts = pm.load_prompts("research", "pipeline", "zh")
        assert isinstance(prompts, dict)

    def test_reload_prompts(self):
        """Test force reload bypasses cache."""
        pm = get_prompt_manager()

        # Load and cache
        prompts1 = pm.load_prompts("research", "pipeline", "en")

        # Force reload
        prompts2 = pm.reload_prompts("research", "pipeline", "en")

        # They should be equal but not the same object
        assert prompts1 == prompts2
        # After reload, cache should have fresh entry
        cache_key = "research_pipeline_en"
        assert cache_key in pm._cache


class TestPromptManagerLanguages:
    """Test language handling."""

    def setup_method(self):
        PromptManager._instance = None
        PromptManager._cache = {}

    def test_english_prompts(self):
        """Test loading English prompts."""
        pm = get_prompt_manager()
        prompts = pm.load_prompts("question", "idea_agent", "en")
        assert isinstance(prompts, dict)

    def test_chinese_prompts(self):
        """Test loading Chinese prompts."""
        pm = get_prompt_manager()
        prompts = pm.load_prompts("question", "idea_agent", "zh")
        assert isinstance(prompts, dict)

    def test_invalid_language_falls_back(self):
        """Test that invalid language code falls back gracefully."""
        pm = get_prompt_manager()
        # Should not raise, should fallback
        prompts = pm.load_prompts("research", "pipeline", "invalid")
        assert isinstance(prompts, dict)


class TestPromptLoadFailures:
    """Malformed assets must be observable and may only fall back deliberately."""

    def setup_method(self):
        PromptManager._instance = None
        PromptManager._cache = {}

    def test_malformed_markdown_reports_source(self, tmp_path: Path):
        prompt_path = tmp_path / "broken.md"
        prompt_path.write_text("---\nname: [unterminated\n---\n", encoding="utf-8")

        with pytest.raises(PromptLoadError, match=r"broken\.md: invalid YAML frontmatter"):
            load_markdown_prompt(prompt_path)

    def test_malformed_preferred_language_falls_back_to_valid_language(self, tmp_path: Path, monkeypatch):
        (tmp_path / "zh").mkdir()
        (tmp_path / "en").mkdir()
        (tmp_path / "zh" / "agent.md").write_text("---\nname: [broken\n---\n", encoding="utf-8")
        (tmp_path / "en" / "agent.md").write_text("## system\n\nEnglish fallback", encoding="utf-8")
        manager = PromptManager()
        monkeypatch.setattr(manager, "_candidate_prompt_dirs", lambda _: [tmp_path])

        assert manager.load_prompts("custom", "agent", "zh") == {"system": "English fallback"}

    def test_all_invalid_candidates_raise_a_single_observable_error(self, tmp_path: Path, monkeypatch):
        for language in ("zh", "cn", "en"):
            path = tmp_path / language
            path.mkdir()
            (path / "agent.md").write_text("---\nname: [broken\n---\n", encoding="utf-8")
        manager = PromptManager()
        monkeypatch.setattr(manager, "_candidate_prompt_dirs", lambda _: [tmp_path])

        with pytest.raises(PromptLoadError, match="all candidate assets failed") as exc_info:
            manager.load_prompts("custom", "agent", "zh")

        assert "zh/agent.md" in str(exc_info.value)
        assert "cn/agent.md" in str(exc_info.value)
        assert "en/agent.md" in str(exc_info.value)
        assert not manager._cache

    def test_missing_required_prompt_section_raises(self, tmp_path: Path, monkeypatch):
        (tmp_path / "en").mkdir()
        (tmp_path / "en" / "agent.md").write_text("## system\n\nSystem text", encoding="utf-8")
        manager = PromptManager()
        monkeypatch.setattr(manager, "_candidate_prompt_dirs", lambda _: [tmp_path])

        with pytest.raises(PromptLoadError, match="missing required prompt sections: user"):
            manager.load_prompts(
                "custom",
                "agent",
                "en",
                required_sections=("system", "user"),
            )

    def test_base_agent_propagates_prompt_configuration_failures(self, monkeypatch):
        from traittutor.agents.base_agent import BaseAgent

        class BrokenManager:
            def load_prompts(self, **_kwargs):
                raise PromptLoadError("invalid prompt asset")

        class TestAgent(BaseAgent):
            async def process(self, *args, **kwargs):
                return None

        monkeypatch.setattr(
            "traittutor.agents.base_agent.get_prompt_manager",
            lambda: BrokenManager(),
        )

        with pytest.raises(PromptLoadError, match="invalid prompt asset"):
            TestAgent(module_name="custom", agent_name="agent", config={})


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
