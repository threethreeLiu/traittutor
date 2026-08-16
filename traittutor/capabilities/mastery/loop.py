"""Mastery path loop-capability hooks."""

from __future__ import annotations

from typing import Any

from traittutor.capabilities.mastery.tools import MASTERY_TOOL_NAMES
from traittutor.capabilities.protocol import PromptBlock
from traittutor.core.context import UnifiedContext
from traittutor.prompts import asset_path
from traittutor.services.prompt.markdown import (
    PromptLoadError,
    load_markdown_prompt,
    nested_prompt_text,
)


class MasteryLoopCapability:
    """Turn-scoped integration for mastery-path tutoring.

    Reuses the full chat tool surface (rag / read_source / ask_user / … under
    the same user toggles as chat) and adds the mastery engine tools on top.
    """

    name = "mastery"
    owned_tools = MASTERY_TOOL_NAMES

    def is_active(self, context: UnifiedContext) -> bool:
        return bool(context.metadata.get("mastery_mode"))

    def system_block(
        self,
        context: UnifiedContext,
        *,
        language: str,
        prompts: dict[str, Any],
    ) -> PromptBlock | None:
        if not self.is_active(context):
            return None
        override = nested_prompt_text(prompts, ("mastery", "system"))
        content = override or _load_system_prompt(language)
        return PromptBlock("mastery_tutor", content)

    def augment_kwargs(
        self,
        tool_name: str,
        kwargs: dict[str, Any],
        context: UnifiedContext,
    ) -> dict[str, Any]:
        if self.is_active(context) and tool_name in MASTERY_TOOL_NAMES:
            updated = dict(kwargs)
            # This is a server-resolved, persisted owner/path/subject link.
            # The model sees neither its fields nor a way to manufacture it;
            # each tool reloads and validates it again before any state read
            # or grading write.
            raw_binding = context.metadata.get("mastery_path_binding")
            updated["_mastery_binding"] = (
                dict(raw_binding) if isinstance(raw_binding, dict) else raw_binding
            )
            updated["_session_id"] = str(context.session_id or "").strip()
            updated["_turn_id"] = str(context.metadata.get("turn_id") or "").strip()
            return updated
        return kwargs

    def pre_loop_seed(self, context: UnifiedContext) -> str:
        _ = context
        return ""


def _load_system_prompt(language: str) -> str:
    lang = "zh" if language.lower().startswith("zh") else "en"
    prompt = asset_path("capabilities", "mastery", lang, "system.md")
    value = load_markdown_prompt(str(prompt)).get("system")
    if not isinstance(value, str) or not value.strip():
        raise PromptLoadError(f"{prompt}: required 'system' prompt section is missing or empty")
    return value


__all__ = ["MasteryLoopCapability"]
