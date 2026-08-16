"""Base abstraction for video-generation adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable

from traittutor.services.videogen.config import VideogenConfig

ProgressFn = Callable[[str], Awaitable[None]]


class BaseVideogenAdapter(ABC):
    """Abstract text-to-video adapter with an asynchronous task lifecycle."""

    @abstractmethod
    async def submit_task(self, prompt: str, config: VideogenConfig) -> str:
        """Submit a generation task and return its provider-owned ID."""

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        config: VideogenConfig,
        *,
        progress: ProgressFn | None = None,
    ) -> tuple[bytes, str]:
        """Generate one video and return its bytes and content type."""


__all__ = ["BaseVideogenAdapter", "ProgressFn"]
