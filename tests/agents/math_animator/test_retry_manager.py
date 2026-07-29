from __future__ import annotations

import asyncio

import pytest

from traittutor.agents.math_animator.models import GeneratedCode, RenderResult, VisualReviewResult
from traittutor.agents.math_animator.renderer import ManimRenderError
from traittutor.agents.math_animator.retry_manager import CodeRetryManager


class _FakeRenderer:
    def __init__(self) -> None:
        self.calls = 0

    async def render(self, *, code: str, output_mode: str, quality: str) -> RenderResult:
        self.calls += 1
        if self.calls == 1:
            raise ManimRenderError("first render failed")
        return RenderResult(output_mode=output_mode, artifacts=[], quality=quality)


@pytest.mark.asyncio
async def test_retry_manager_emits_status_and_recovers() -> None:
    renderer = _FakeRenderer()
    status_messages: list[str] = []

    async def _on_status(message: str) -> None:
        status_messages.append(message)

    async def _repair_callback(code: str, error_message: str, attempt: int) -> GeneratedCode:
        assert "failed" in error_message
        assert attempt == 1
        return GeneratedCode(code=code + "\n# repaired", rationale="patched")

    manager = CodeRetryManager(
        renderer=renderer,
        repair_callback=_repair_callback,
        on_status=_on_status,
        max_retries=2,
    )

    final_code, result = await manager.render_with_retries(
        initial_code="from manim import *",
        output_mode="video",
        quality="medium",
    )

    assert final_code.endswith("# repaired")
    assert result.retry_attempts == 1
    assert status_messages == [
        "Starting render attempt 1/3.",
        "Generating repaired code for retry #1.",
        "Retry #1 code generated. Re-rendering now.",
        "Starting render attempt 2/3.",
    ]


@pytest.mark.asyncio
async def test_retry_manager_times_out_repair() -> None:
    class _AlwaysFailRenderer:
        async def render(self, *, code: str, output_mode: str, quality: str) -> RenderResult:
            raise ManimRenderError("render failed")

    async def _slow_repair(_code: str, _error_message: str, _attempt: int) -> GeneratedCode:
        await asyncio.sleep(0.05)
        return GeneratedCode(code="pass", rationale="slow")

    manager = CodeRetryManager(
        renderer=_AlwaysFailRenderer(),
        repair_callback=_slow_repair,
        max_retries=1,
        repair_timeout_seconds=0.01,
    )

    with pytest.raises(ManimRenderError, match="timed out"):
        await manager.render_with_retries(
            initial_code="from manim import *",
            output_mode="video",
            quality="medium",
        )


@pytest.mark.asyncio
async def test_retry_manager_retries_on_visual_review_failure() -> None:
    class _PassRenderer:
        def __init__(self) -> None:
            self.calls = 0

        async def render(self, *, code: str, output_mode: str, quality: str) -> RenderResult:
            self.calls += 1
            return RenderResult(output_mode=output_mode, artifacts=[], quality=quality)

    renderer = _PassRenderer()
    review_calls = 0

    async def _review_callback(code: str, _render_result: RenderResult) -> VisualReviewResult:
        nonlocal review_calls
        review_calls += 1
        if review_calls == 1:
            assert "# repaired" not in code
            return VisualReviewResult(
                passed=False,
                summary="Labels overlap with the triangle edges.",
                suggested_fix="Move labels outward and increase spacing.",
                reviewed_frames=3,
            )
        assert code.endswith("# repaired")
        return VisualReviewResult(
            passed=True,
            summary="Visuals are clear.",
            reviewed_frames=3,
        )

    async def _repair_callback(code: str, error_message: str, attempt: int) -> GeneratedCode:
        assert "Visual review failed" in error_message
        assert "Move labels outward" in error_message
        assert attempt == 1
        return GeneratedCode(code=code + "\n# repaired", rationale="layout fix")

    manager = CodeRetryManager(
        renderer=renderer,
        repair_callback=_repair_callback,
        review_callback=_review_callback,
        max_retries=2,
    )

    final_code, result = await manager.render_with_retries(
        initial_code="from manim import *",
        output_mode="video",
        quality="medium",
    )

    assert review_calls == 2
    assert renderer.calls == 2
    assert final_code.endswith("# repaired")
    assert result.visual_review is not None
    assert result.visual_review.passed is True
    assert result.retry_attempts == 1


@pytest.mark.asyncio
async def test_retry_manager_returns_best_effort_result_when_visual_review_still_fails() -> None:
    class _PassRenderer:
        async def render(self, *, code: str, output_mode: str, quality: str) -> RenderResult:
            return RenderResult(output_mode=output_mode, artifacts=[], quality=quality)

    status_messages: list[str] = []

    async def _on_status(message: str) -> None:
        status_messages.append(message)

    async def _review_callback(_code: str, _render_result: RenderResult) -> VisualReviewResult:
        return VisualReviewResult(
            passed=False,
            summary="Camera framing still cuts off labels.",
            suggested_fix="Zoom out and keep labels inside frame.",
            reviewed_frames=3,
        )

    async def _repair_callback(code: str, _error_message: str, _attempt: int) -> GeneratedCode:
        return GeneratedCode(code=code + "\n# repaired", rationale="frame fix")

    manager = CodeRetryManager(
        renderer=_PassRenderer(),
        repair_callback=_repair_callback,
        review_callback=_review_callback,
        on_status=_on_status,
        max_retries=1,
    )

    final_code, result = await manager.render_with_retries(
        initial_code="from manim import *",
        output_mode="video",
        quality="medium",
    )

    assert final_code.endswith("# repaired")
    assert result.visual_review is not None
    assert result.visual_review.passed is False
    assert result.visual_review.summary == "Camera framing still cuts off labels."
    assert result.retry_attempts == 1
    assert status_messages[-1] == (
        "Visual review still found issues after all retries. Returning the best available result with a warning."
    )
