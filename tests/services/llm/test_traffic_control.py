"""Regression tests for provider traffic-control resource release."""

from __future__ import annotations

import pytest

from traittutor.services.llm.traffic_control import TrafficController


@pytest.mark.asyncio
@pytest.mark.parametrize("raise_inside", [False, True])
async def test_context_manager_releases_slot_on_every_exit(raise_inside: bool) -> None:
    controller = TrafficController(
        "test-provider",
        max_concurrency=1,
        requests_per_minute=60,
    )

    if raise_inside:
        with pytest.raises(RuntimeError, match="provider failure"):
            async with controller:
                raise RuntimeError("provider failure")
    else:
        async with controller:
            pass

    # A second acquisition proves that __aexit__ followed the async context
    # manager protocol and released the sole slot on both paths.
    async with controller:
        pass
