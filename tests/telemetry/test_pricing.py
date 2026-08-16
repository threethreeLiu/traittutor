"""Tests for explicit, exact-model operational pricing."""

from __future__ import annotations

import json
from pathlib import Path

from traittutor.telemetry.pricing import (
    FileTokenPricing,
    NoOpTokenPricing,
    create_configured_token_pricing,
)


def _pricing_document() -> dict[str, object]:
    return {
        "version": "2026-08-10",
        "models": {
            "server-model": {
                "input_picousd_per_million_tokens": 2_000_000_000_000,
                "output_picousd_per_million_tokens": 4_000_000_000_000,
            }
        },
    }


def test_file_pricing_is_exact_model_and_usage_bound(tmp_path: Path) -> None:
    path = tmp_path / "prices.json"
    path.write_text(json.dumps(_pricing_document()), encoding="utf-8")
    pricing = create_configured_token_pricing({"TRAITTUTOR_TELEMETRY_PRICING_PATH": str(path)})

    assert isinstance(pricing, FileTokenPricing)
    assert pricing.version == "2026-08-10"
    assert (
        pricing.cost_picousd("server-model", {"prompt_tokens": 10, "completion_tokens": 5})
        == 40_000_000
    )
    assert (
        pricing.cost_picousd("unknown-model", {"prompt_tokens": 10, "completion_tokens": 5}) is None
    )
    assert pricing.cost_picousd("server-model", {"total_tokens": 15}) is None
    assert (
        pricing.cost_picousd("server-model", {"prompt_tokens": -1, "completion_tokens": 5}) is None
    )


def test_invalid_or_missing_pricing_is_noop(tmp_path: Path) -> None:
    malformed = tmp_path / "bad-prices.json"
    malformed.write_text('{"models":{}}', encoding="utf-8")

    assert isinstance(create_configured_token_pricing({}), NoOpTokenPricing)
    assert isinstance(
        create_configured_token_pricing({"TRAITTUTOR_TELEMETRY_PRICING_PATH": str(malformed)}),
        NoOpTokenPricing,
    )
