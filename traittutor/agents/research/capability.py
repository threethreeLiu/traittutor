"""Deep Research capability — agentic-engine-based deep research.

Thin shim that delegates to :class:`ResearchPipeline`. All orchestration
— rephrase (mini agentic loop with ``ask_user``), decompose, per-block
research loops with ``THINK`` / ``TOOL`` / ``APPEND`` / ``FINISH``,
queue scheduler, and iterative reporting — lives in the pipeline
module. The capability only handles:

* request-config validation,
* the outline-preview two-stage flow (first call returns sub-topics
  the user edits / confirms; second call drives Phase 3+4 with the
  confirmed outline).

Tool composition is delegated to the shared
:mod:`traittutor.agents._shared.tool_composition` policy — same as chat,
so the user's composer toggles + the attached KB drive what the per-block
research loop actually has access to. There is no separate "sources"
knob.
"""

from __future__ import annotations

from traittutor.agents.research.pipeline import ResearchPipeline, SubTopicItem
from traittutor.agents.research.request_config import (
    build_research_runtime_config,
    validate_research_request_config,
)
from traittutor.core.capability_protocol import BaseCapability, CapabilityManifest
from traittutor.core.context import UnifiedContext
from traittutor.core.stream_bus import StreamBus
from traittutor.runtime.request_contracts import get_capability_request_schema
from traittutor.services.config import load_config_with_main


class DeepResearchCapability(BaseCapability):
    manifest = CapabilityManifest(
        name="deep_research",
        description="Agentic-loop deep research with iterative report generation.",
        stages=["rephrasing", "decomposing", "researching", "reporting"],
        tools_used=["rag", "web_search", "paper_search", "code_execution"],
        cli_aliases=["research"],
        request_schema=get_capability_request_schema("deep_research"),
    )

    async def run(self, context: UnifiedContext, stream: StreamBus) -> None:
        kb_name = context.knowledge_bases[0] if context.knowledge_bases else None
        request_config = validate_research_request_config(context.config_overrides)

        enabled_tools = list(context.enabled_tools or [])
        runtime_config = build_research_runtime_config(
            base_config=load_config_with_main("main.yaml"),
            request_config=request_config,
            kb_name=kb_name,
        )

        # Outline-preview two-stage flow: first call lacks a confirmed
        # outline → pipeline returns ``outline_preview`` and exits; the
        # frontend surfaces the outline editor and (after the user
        # confirms) sends a second call with ``confirmed_outline`` set.
        confirmed_outline_items: list[SubTopicItem] | None = None
        if request_config.confirmed_outline is not None:
            confirmed_outline_items = [
                SubTopicItem(title=item.title, overview=item.overview or "")
                for item in request_config.confirmed_outline
            ]

        pipeline = ResearchPipeline(
            language=context.language,
            runtime_config=runtime_config,
            kb_name=kb_name,
            enabled_tools=enabled_tools,
        )
        result = await pipeline.run(
            context=context,
            topic=context.user_message,
            confirmed_outline=confirmed_outline_items,
            attachments=context.attachments,
            stream=stream,
        )

        # Chat is the product surface for research.  The planning result is
        # therefore an internal, visible-to-the-user demand-analysis stage,
        # not a separate outline editor that blocks execution.  Reuse the
        # generated outline immediately and let the normal research/reporting
        # path stream its work and final answer into this same conversation.
        if result.get("outline_preview"):
            planned_outline = [
                SubTopicItem(
                    title=str(item.get("title") or "").strip(),
                    overview=str(item.get("overview") or "").strip(),
                )
                for item in result.get("sub_topics", [])
                if isinstance(item, dict) and str(item.get("title") or "").strip()
            ]
            planning_message = (
                "需求已分析，正在按学习目标组织资料与来源。"
                if context.language.lower().startswith("zh")
                else "Requirements analyzed. Organizing sources around your learning goal."
            )
            await stream.progress(
                planning_message,
                source=self.name,
                stage="decomposing",
                metadata={
                    "trace_kind": "planning",
                    "topic": result.get("topic") or context.user_message,
                    "subtopic_count": len(planned_outline),
                },
            )
            if planned_outline:
                await pipeline.run(
                    context=context,
                    topic=str(result.get("topic") or context.user_message),
                    confirmed_outline=planned_outline,
                    attachments=context.attachments,
                    stream=stream,
                )
