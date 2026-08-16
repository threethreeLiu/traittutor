"""Server-side helpers for preserving explicit model overrides at the Gateway."""

from __future__ import annotations

from traittutor.services.llm.config import LLMConfig, get_llm_config
from traittutor.services.llm.exceptions import LLMConfigError
from traittutor.services.llm.utils import is_local_llm_server
from traittutor.services.provider_registry import (
    ProviderSpec,
    canonical_provider_name,
    find_by_model,
    find_by_name,
    find_gateway,
)


def _resolve_provider(
    *,
    current: LLMConfig,
    model: str,
    api_key: str,
    effective_url: str | None,
    binding: str | None,
    routing_changed: bool,
) -> ProviderSpec | None:
    if not routing_changed:
        return find_by_name(current.provider_name) or find_by_name(current.binding)

    binding_hint = canonical_provider_name(binding) or binding
    explicit = find_by_name(binding_hint) if binding_hint else None
    gateway = find_gateway(
        provider_name=explicit.name if explicit is not None else None,
        api_key=api_key or None,
        api_base=effective_url or None,
    )
    if explicit is not None and gateway is not None and explicit.name == "openai":
        return gateway
    if explicit is not None:
        return explicit
    if gateway is not None:
        return gateway

    model_provider = find_by_model(model)
    if model_provider is not None:
        return model_provider
    if is_local_llm_server(effective_url or ""):
        if effective_url and "11434" in effective_url:
            return find_by_name("ollama") or find_by_name("vllm")
        return find_by_name("vllm") or find_by_name("ollama")
    return find_by_name(current.provider_name) or find_by_name(current.binding)


def gateway_config_with_overrides(
    *,
    base: LLMConfig | None = None,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    api_version: str | None = None,
    binding: str | None = None,
    reasoning_effort: str | None = None,
    extra_headers: dict[str, str] | None = None,
) -> LLMConfig:
    """Return one request-scoped config without changing the active profile."""
    standalone = False
    if base is not None:
        current = base
    else:
        try:
            current = get_llm_config()
        except (LLMConfigError, ValueError):
            if not model:
                raise
            standalone = True
            # Explicit tool and integration calls remain valid before an active
            # Catalog profile exists. This seed contains request-local values
            # only; provider inference below still owns the final route.
            seed_binding = canonical_provider_name(binding) or binding or "openai"
            current = LLMConfig(
                model=model,
                api_key=api_key or "",
                base_url=base_url,
                effective_url=base_url,
                api_version=api_version,
                binding=seed_binding,
                provider_name=seed_binding,
                reasoning_effort=reasoning_effort,
                extra_headers=dict(extra_headers or {}),
            )
    binding_hint = canonical_provider_name(binding) or binding
    resolved_model = model or current.model
    resolved_api_key = current.api_key if api_key is None else api_key
    resolved_base_url = current.base_url if base_url is None else base_url
    resolved_effective_url = current.effective_url if base_url is None else base_url
    routing_changed = standalone or (
        (binding_hint is not None and binding_hint not in {current.binding, current.provider_name})
        or (model is not None and model != current.model)
        or (api_key is not None and api_key != current.api_key)
        or (base_url is not None and base_url not in {current.base_url, current.effective_url})
    )
    spec = _resolve_provider(
        current=current,
        model=resolved_model,
        api_key=resolved_api_key,
        effective_url=resolved_effective_url,
        binding=binding_hint,
        routing_changed=routing_changed,
    )
    current_spec = find_by_name(current.provider_name) or find_by_name(current.binding)
    if (
        base_url is None
        and spec is not None
        and spec.default_api_base
        and (current_spec is None or spec.name != current_spec.name)
    ):
        resolved_base_url = spec.default_api_base
        resolved_effective_url = spec.default_api_base
    resolved_binding = spec.name if spec is not None else binding_hint or current.binding
    headers = dict(current.extra_headers or {})
    if extra_headers:
        headers.update(extra_headers)
    return current.model_copy(
        {
            "model": resolved_model,
            "api_key": resolved_api_key,
            "base_url": resolved_base_url,
            "effective_url": resolved_effective_url,
            "api_version": current.api_version if api_version is None else api_version,
            "binding": resolved_binding,
            "provider_name": spec.name if spec is not None else current.provider_name,
            "provider_mode": spec.mode if spec is not None else current.provider_mode,
            "reasoning_effort": (
                current.reasoning_effort if reasoning_effort is None else reasoning_effort
            ),
            "extra_headers": headers,
        }
    )


__all__ = ["gateway_config_with_overrides"]
