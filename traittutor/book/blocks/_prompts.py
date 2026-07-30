"""
Shared prompt loader for non-BaseAgent call sites in ``traittutor/book``.

All LLM-facing prompts inside this module live as Markdown prompt assets under
``traittutor/book/prompts/{en,zh}/<name>.md`` and are loaded through the
unified :class:`~traittutor.services.prompt.PromptManager`. This helper is the
thin wrapper that block generators and the SectionArchitect use, since they
call ``llm_text`` directly instead of inheriting :class:`BaseAgent`.

Conventions
-----------
* One prompt file per logical writer (block generator, planner stage…), named
  after the python module (``text.py`` → ``text.md``).
* Top-level keys are short identifiers (``system``, ``user_template``,
  ``bridge_system``, ``outline_system``…) and contain plain strings.
* Variable interpolation uses ``str.format`` placeholders (``{chapter_title}``).

Missing files / keys raise :class:`RuntimeError` so the failure surfaces at
the call site instead of silently producing degenerate prompts.
"""

from __future__ import annotations

from typing import Any

from traittutor.services.prompt import get_prompt_manager


def load_book_prompts(name: str, language: str) -> dict[str, Any]:
    """Load a Markdown prompt bundle for the ``book`` module.

    Args:
        name: File stem under ``traittutor/book/prompts/{lang}/``
            (e.g. ``"text"``, ``"section"``, ``"page_planner"``).
        language: ``"en"`` or ``"zh"``.

    Returns:
        Parsed prompt asset as a dictionary.

    Raises:
        RuntimeError: When the bundle is missing or empty.
    """
    prompts = get_prompt_manager().load_prompts(
        module_name="book",
        agent_name=name,
        language=language,
    )
    if not prompts:
        raise RuntimeError(
            f"Missing prompt bundle for book/{name} (language={language}). "
            f"Expected traittutor/book/prompts/{{en,zh}}/{name}.md."
        )
    return prompts


def get_book_prompt(prompts: dict[str, Any], key: str) -> str:
    """Return the prompt string under ``key`` from a loaded bundle.

    Raises ``RuntimeError`` if the key is missing or not a non-empty string.
    """
    value = prompts.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"Prompt key '{key}' missing or empty in loaded book prompt bundle.")
    return value


__all__ = ["load_book_prompts", "get_book_prompt"]
