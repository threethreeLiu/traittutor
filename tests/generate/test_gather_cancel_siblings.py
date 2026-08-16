"""Regression: parallel generation batches cancel siblings on first failure.

A plain ``asyncio.gather`` re-raises the first exception but leaves sibling
coroutines running unobserved — they keep burning provider quota after the
overall generation already failed. ``_gather_cancel_siblings`` must cancel
the rest and re-raise the original error unchanged.
"""

from __future__ import annotations

import asyncio

import pytest

from traittutor.generate.service import _gather_cancel_siblings


async def _slow(value: int, delay: float = 0.2) -> int:
    await asyncio.sleep(delay)
    return value


async def _boom() -> int:
    raise RuntimeError("provider exploded")


@pytest.mark.asyncio
async def test_success_returns_results_in_order() -> None:
    results = await _gather_cancel_siblings(_slow(index, delay=0.01 * index) for index in range(4))
    assert results == [0, 1, 2, 3]


@pytest.mark.asyncio
async def test_first_failure_cancels_siblings_and_reraises_original() -> None:
    cancelled: list[int] = []

    async def slow_child(index: int) -> int:
        try:
            await asyncio.sleep(5)
            return index
        except asyncio.CancelledError:
            cancelled.append(index)
            raise

    async def failing_child() -> int:
        await asyncio.sleep(0.01)
        raise RuntimeError("provider exploded")

    with pytest.raises(RuntimeError, match="provider exploded"):
        await _gather_cancel_siblings(
            [
                slow_child(1),
                failing_child(),
                slow_child(2),
            ]
        )
    # Both slow siblings were cancelled promptly, not left running.
    assert sorted(cancelled) == [1, 2]


@pytest.mark.asyncio
async def test_outer_cancellation_cancels_children() -> None:
    cancelled = asyncio.Event()

    async def slow_child() -> int:
        try:
            await asyncio.sleep(5)
            return 0
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def outer() -> None:
        await _gather_cancel_siblings([slow_child(), slow_child()])

    task = asyncio.ensure_future(outer())
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()
