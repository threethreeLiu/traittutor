"""Server-owned, opt-in token pricing for aggregate operational telemetry.

This module deliberately does not ship a mutable public price table. Providers
change pricing independently of this repository, so an operator must opt in
with a versioned local JSON file and a model-exact record. Unknown models or
incomplete usage produce no estimate rather than a plausible-looking cost.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
from typing import Any, Mapping, Protocol

logger = logging.getLogger(__name__)

TELEMETRY_PRICING_PATH_ENV = "TRAITTUTOR_TELEMETRY_PRICING_PATH"
_MAX_PRICING_BYTES = 512 * 1024
_PRICE_DENOMINATOR = 1_000_000


class TokenPricing(Protocol):
    """Return a deterministic operational cost or ``None`` when unpriced."""

    def cost_picousd(self, model: str, usage: Mapping[str, int]) -> int | None:
        """Compute a non-negative pico-USD total from provider token counters."""


class NoOpTokenPricing:
    """Default: do not estimate a price without explicit deployment input."""

    def cost_picousd(self, model: str, usage: Mapping[str, int]) -> int | None:
        del model, usage
        return None


@dataclass(frozen=True)
class ModelTokenPrice:
    """One exact model's pico-USD price per million input/output tokens."""

    input_picousd_per_million_tokens: int
    output_picousd_per_million_tokens: int

    def __post_init__(self) -> None:
        for value in (
            self.input_picousd_per_million_tokens,
            self.output_picousd_per_million_tokens,
        ):
            if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 10**18:
                raise ValueError("Token price must be a bounded non-negative integer")


@dataclass(frozen=True)
class FileTokenPricing:
    """Validated exact-model pricing loaded from a deployment-owned JSON file."""

    version: str
    models: Mapping[str, ModelTokenPrice]

    def cost_picousd(self, model: str, usage: Mapping[str, int]) -> int | None:
        price = self.models.get(model)
        if price is None:
            return None
        input_tokens = _usage_counter(usage, "prompt_tokens", "input_tokens")
        output_tokens = _usage_counter(usage, "completion_tokens", "output_tokens")
        if input_tokens is None or output_tokens is None:
            return None
        return (
            input_tokens * price.input_picousd_per_million_tokens
            + output_tokens * price.output_picousd_per_million_tokens
        ) // _PRICE_DENOMINATOR


def create_configured_token_pricing(
    environ: Mapping[str, str] | None = None,
) -> TokenPricing:
    """Load explicit server pricing or fall back to no pricing safely.

    The pathname is deployment configuration, never a request value. It must
    resolve to a bounded regular file. Invalid, unreadable, oversized, or
    malformed data does not interrupt a learner request and cannot create a
    guessed cost.
    """
    source = os.environ if environ is None else environ
    raw_path = source.get(TELEMETRY_PRICING_PATH_ENV, "").strip()
    if not raw_path:
        return NoOpTokenPricing()
    try:
        path = Path(raw_path).resolve(strict=True)
        if not path.is_file() or path.stat().st_size > _MAX_PRICING_BYTES:
            raise ValueError("pricing path must be a bounded regular file")
        payload = json.loads(path.read_text(encoding="utf-8"))
        return _parse_pricing(payload)
    except Exception as exc:  # noqa: BLE001 - deployment config must fail open
        logger.warning("telemetry_pricing_disabled error_type=%s", type(exc).__name__)
        return NoOpTokenPricing()


def _parse_pricing(value: Any) -> FileTokenPricing:
    if not isinstance(value, Mapping):
        raise ValueError("pricing document must be an object")
    version = value.get("version")
    raw_models = value.get("models")
    if not isinstance(version, str) or not version or len(version) > 64:
        raise ValueError("pricing version is required")
    if not isinstance(raw_models, Mapping) or not raw_models or len(raw_models) > 512:
        raise ValueError("pricing models must be a bounded object")
    models: dict[str, ModelTokenPrice] = {}
    for model, raw_price in raw_models.items():
        if not isinstance(model, str) or not model or len(model) > 255:
            raise ValueError("pricing model key is invalid")
        if not isinstance(raw_price, Mapping):
            raise ValueError("pricing model record is invalid")
        models[model] = ModelTokenPrice(
            input_picousd_per_million_tokens=_integer_field(
                raw_price, "input_picousd_per_million_tokens"
            ),
            output_picousd_per_million_tokens=_integer_field(
                raw_price, "output_picousd_per_million_tokens"
            ),
        )
    return FileTokenPricing(version=version, models=models)


def _integer_field(value: Mapping[str, Any], name: str) -> int:
    result = value.get(name)
    if not isinstance(result, int) or isinstance(result, bool):
        raise ValueError(f"pricing {name} must be an integer")
    return result


def _usage_counter(usage: Mapping[str, int], primary: str, alternate: str) -> int | None:
    for key in (primary, alternate):
        value = usage.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    return None


__all__ = [
    "FileTokenPricing",
    "ModelTokenPrice",
    "NoOpTokenPricing",
    "TELEMETRY_PRICING_PATH_ENV",
    "TokenPricing",
    "create_configured_token_pricing",
]
