from __future__ import annotations

from traittutor.orchestration import build_executor_map


def test_executor_map_uses_injected_real_adapters() -> None:
    async def adapter(*args: object) -> object:
        del args
        return object()

    mapping = build_executor_map(
        material=adapter,
        courseware=adapter,  # type: ignore[arg-type]
        practice=adapter,  # type: ignore[arg-type]
        srl=adapter,  # type: ignore[arg-type]
        visual=adapter,  # type: ignore[arg-type]
        ui_composer=adapter,  # type: ignore[arg-type]
        evaluator=adapter,  # type: ignore[arg-type]
    )
    assert mapping == {
        "material": adapter,
        "instruction": adapter,
        "practice": adapter,
        "srl": adapter,
        "visual": adapter,
        "ui_composer": adapter,
        "evaluator": adapter,
    }
