"""Provider model-list discovery; this performs no model inference."""

from __future__ import annotations

from .utils import is_local_llm_server


async def fetch_models(
    binding: str,
    base_url: str,
    api_key: str | None = None,
) -> list[str]:
    if is_local_llm_server(base_url):
        from . import local_provider

        return await local_provider.fetch_models(base_url, api_key)

    from . import cloud_provider

    return await cloud_provider.fetch_models(base_url, api_key, binding)


__all__ = ["fetch_models"]
