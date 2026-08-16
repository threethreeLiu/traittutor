#!/usr/bin/env python3
"""Run a redacted real-provider complete/stream Gateway smoke."""

from __future__ import annotations

import asyncio
import json

from traittutor.gateway.provider_smoke import verify_gateway_provider


async def _run() -> int:
    result = await verify_gateway_provider()
    print(
        json.dumps(
            {
                "complete": {
                    "model": result.complete_model,
                    "latency_ms": result.complete_latency_ms,
                    "total_tokens": result.complete_total_tokens,
                },
                "stream": {
                    "model": result.stream_model,
                    "latency_ms": result.stream_latency_ms,
                    "total_tokens": result.stream_total_tokens,
                },
                "status": "ok",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
