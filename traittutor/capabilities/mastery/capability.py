"""Mastery Path capability — mastery-based tutoring driven by the chat loop.

There is no bespoke state machine here anymore. The chat agent loop IS the
tutor: this capability only marks the turn as mastery mode and resolves the
active path id, then runs the standard agentic chat pipeline. The pipeline
mounts the mastery tools (``mastery_status`` / ``mastery_quiz`` /
``mastery_grade`` / ``mastery_assess`` / ``mastery_resume`` / ``mastery_build``) and injects the
tutor playbook; the pure engine in :mod:`traittutor.learning` owns the hard,
per-type mastery gate and the spaced-repetition arithmetic.

Design axiom (shared with chat): the intelligence lives at the loop's exit —
the model decides what to teach and how to question — while the gate that
decides *whether the learner may advance* is a deterministic engine call.
"""

from __future__ import annotations

from traittutor.agents.chat.agentic_pipeline import AgenticChatPipeline
from traittutor.capabilities.mastery.binding import load_bound_mastery_progress
from traittutor.capabilities.mastery.tools import MASTERY_TOOL_NAMES
from traittutor.core.capability_protocol import BaseCapability, CapabilityManifest
from traittutor.core.context import UnifiedContext
from traittutor.core.stream_bus import StreamBus
from traittutor.runtime.request_contracts import get_capability_request_schema


class MasteryPathCapability(BaseCapability):
    manifest = CapabilityManifest(
        name="mastery_path",
        description=(
            "Mastery-based tutoring: the chat agent loop drives an adaptive "
            "mastery path with a hard, per-type mastery gate and spaced review."
        ),
        stages=["responding"],
        tools_used=[*MASTERY_TOOL_NAMES, "rag", "read_source", "ask_user"],
        cli_aliases=["mastery"],
        request_schema=get_capability_request_schema("mastery_path"),
    )

    async def run(self, context: UnifiedContext, stream: StreamBus) -> None:
        resolved = load_bound_mastery_progress(context.metadata.get("mastery_path_binding"))
        if resolved is None:
            # A path can never be inferred from this chat/session/model input.
            # Keep the failure user-visible but side-effect free; in
            # particular do not mount mastery tools or create a progress file.
            await stream.error(
                "This mastery chat has no verified learning-path subject binding. "
                "Select an existing confirmed learning path before practising.",
                source=self.name,
            )
            return
        binding, _progress = resolved
        context.metadata["mastery_mode"] = True
        # Canonical runtime context is supplied by TurnRuntimeManager.  Keep
        # only the server-verified values; no book/session fallback exists.
        context.metadata["mastery_path_binding"] = binding.model_dump()
        pipeline = AgenticChatPipeline(language=context.language)
        await pipeline.run(context, stream)


__all__ = ["MasteryPathCapability"]
