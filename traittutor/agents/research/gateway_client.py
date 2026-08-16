"""Research-scoped facade over the shared typed Gateway agentic adapter."""

from __future__ import annotations

import asyncio
from typing import Any

from traittutor.core.agentic.gateway_client import GatewayAgenticClient


class GatewayResearchClient(GatewayAgenticClient):
    """Preserve the ResearchPipeline client name while enforcing Gateway I/O."""

    def __init__(
        self,
        *,
        owner_id: str,
        cancellation_event: asyncio.Event,
        llm_config: Any,
        reasoning_effort: str | None,
        timeout_seconds: float = 180.0,
    ) -> None:
        super().__init__(
            owner_id=owner_id,
            cancellation_event=cancellation_event,
            llm_config=llm_config,
            reasoning_effort=reasoning_effort,
            purpose_prefix="research",
            surface_name="Deep Research",
            timeout_seconds=timeout_seconds,
        )


__all__ = ["GatewayResearchClient"]
