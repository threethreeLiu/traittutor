"""Video-generation adapter registry."""

from __future__ import annotations

from traittutor.services.generation_http import GenerationProviderError
from traittutor.services.videogen.adapters.agnes import AgnesVideogenAdapter
from traittutor.services.videogen.base import BaseVideogenAdapter

VIDEOGEN_ADAPTERS: dict[str, BaseVideogenAdapter] = {"agnes": AgnesVideogenAdapter()}


def get_videogen_adapter(name: str) -> BaseVideogenAdapter:
    adapter = VIDEOGEN_ADAPTERS.get(name or "agnes")
    if adapter is None:
        raise GenerationProviderError(f"Unsupported videogen adapter: {name!r}")
    return adapter


__all__ = ["VIDEOGEN_ADAPTERS", "get_videogen_adapter"]
