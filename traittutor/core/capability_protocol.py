"""
Capability Protocol
===================

Base class for the Capability layer (Level 2).
Capabilities are multi-step agent pipelines invoked when the user selects
a deep mode (e.g. Deep Solve, Deep Question).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from .context import UnifiedContext
from .stream_bus import StreamBus


@dataclass
class CapabilityManifest:
    """Static metadata for a capability."""

    name: str
    description: str
    stages: list[str] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)
    cli_aliases: list[str] = field(default_factory=list)
    request_schema: dict[str, Any] = field(default_factory=dict)
    config_defaults: dict[str, Any] = field(default_factory=dict)


class BaseCapability(ABC):
    """
    Abstract base for all capabilities (deep modes).

    Subclasses must provide ``manifest`` and implement ``run``.

    Example::

        class MySolverCapability(BaseCapability):
            manifest = CapabilityManifest(
                name="deep_solve",
                description="Multi-agent problem solving.",
                stages=["planning", "reasoning", "writing"],
                tools_used=["rag", "web_search", "code_execution"],
            )

            async def run(self, context, stream):
                async with stream.stage("planning", source=self.manifest.name):
                    plan = await self._plan(context)
                ...
    """

    manifest: CapabilityManifest

    @abstractmethod
    async def run(self, context: UnifiedContext, stream: StreamBus) -> None:
        """Execute the full capability pipeline, emitting events to *stream*."""
        ...

    @property
    def name(self) -> str:
        return self.manifest.name

    @property
    def stages(self) -> list[str]:
        return self.manifest.stages
