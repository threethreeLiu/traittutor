"""Resolved runtime configuration for video-generation providers."""

from __future__ import annotations

from dataclasses import dataclass, field

from traittutor.services.generation_http import AUTH_BEARER


@dataclass(slots=True)
class VideogenConfig:
    """Resolved text-to-video configuration for one generation call."""

    model: str
    provider_name: str = "agnes"
    adapter: str = "agnes"
    auth_style: str = AUTH_BEARER
    api_key: str = ""
    base_url: str = ""
    api_version: str | None = None
    extra_headers: dict[str, str] = field(default_factory=dict)
    aspect_ratio: str = ""
    duration: str = ""
    resolution: str = ""
    request_timeout: int = 60
    poll_interval: float = 5.0
    poll_timeout: int = 600


__all__ = ["VideogenConfig"]
