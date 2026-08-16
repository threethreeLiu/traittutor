"""
Prompt Service
==============

Unified prompt management for all TraitTutor modules.

Usage:
    from traittutor.services.prompt import get_prompt_manager, PromptManager

    # Get singleton manager
    pm = get_prompt_manager()

    # Load prompts for an agent
    prompts = pm.load_prompts("solve", "solve_agent", language="en")

    # Get specific prompt
    system_prompt = pm.get_prompt(prompts, "system", "base")

The manager is imported lazily: it pulls in ``services.config``, and several
low-level modules (tool hints, capabilities) import the Markdown prompt parser
from this package during package initialisation — an eager manager import
would close a circular-import loop.
"""

from .language import (
    append_language_directive,
    language_directive,
    language_label,
    normalize_language,
)
from .markdown import (
    PromptLoadError,
    dump_markdown_prompt,
    load_markdown_prompt,
    nested_prompt_text,
    parse_markdown_prompt,
)

_LAZY_MANAGER_ATTRS = {"PromptManager", "get_prompt_manager"}


def __getattr__(name: str):
    if name in _LAZY_MANAGER_ATTRS:
        from .manager import PromptManager, get_prompt_manager

        return {"PromptManager": PromptManager, "get_prompt_manager": get_prompt_manager}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "PromptManager",
    "PromptLoadError",
    "append_language_directive",
    "dump_markdown_prompt",
    "get_prompt_manager",
    "language_directive",
    "language_label",
    "load_markdown_prompt",
    "nested_prompt_text",
    "normalize_language",
    "parse_markdown_prompt",
]
