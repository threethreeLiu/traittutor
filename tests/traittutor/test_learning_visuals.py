from __future__ import annotations

import pytest

from traittutor.generate import visuals


def _targets() -> dict[str, object]:
    return {
        "visual_targets": [
            {"concept_id": "marketing.keyword", "label": "关键词", "priority": "review"}
        ]
    }


def test_learning_visual_gate_skips_without_visual_targets() -> None:
    decision = visuals.should_generate_learning_visual(
        slr_support={
            "dimensions": {
                "goal_planning": {
                    "label": "目标与计划",
                    "emphasis": "strong",
                    "actions": ["用清单标记完成进度"],
                }
            }
        },
        learning_targets={"visual_targets": []},
        generation_type="courseware",
    )

    assert decision["should_generate"] is False
    assert decision["reason"] == "no_visual_targets"


def test_learning_visual_gate_requires_slr_visual_support_need() -> None:
    standard = visuals.should_generate_learning_visual(
        slr_support={
            "dimensions": {
                "goal_planning": {
                    "label": "目标与计划",
                    "emphasis": "standard",
                    "actions": ["用清单标记完成进度"],
                }
            },
            "generation_support_profile": {
                "learner_support_profile": {
                    "structure_need": 3,
                    "scaffolding_need": 2,
                    "conceptual_depth_readiness": 3,
                }
            },
        },
        learning_targets=_targets(),
        generation_type="flashcards",
    )
    strong = visuals.should_generate_learning_visual(
        slr_support={
            "dimensions": {
                "monitoring_regulation": {
                    "label": "监控与调节",
                    "emphasis": "strong",
                    "actions": ["遇到卡点时使用恢复提示"],
                }
            },
            "generation_support_profile": {
                "learner_support_profile": {
                    "structure_need": 5,
                    "scaffolding_need": 4,
                    "conceptual_depth_readiness": 3,
                }
            },
        },
        learning_targets=_targets(),
        generation_type="quiz",
    )

    assert standard["should_generate"] is False
    assert standard["reason"] == "no_slr_visual_support"
    assert strong["should_generate"] is True
    assert strong["reason"] == "slr_visual_support"
    assert "监控与调节" in strong["support_reasons"]
    assert "structure_need" in strong["support_reasons"]


def test_learning_visual_gate_tolerates_non_numeric_support_values() -> None:
    decision = visuals.should_generate_learning_visual(
        slr_support={
            "dimensions": {},
            "generation_support_profile": {
                "learner_support_profile": {
                    "structure_need": "high",
                    "scaffolding_need": None,
                    "conceptual_depth_readiness": "4.5",
                }
            },
        },
        learning_targets=_targets(),
        generation_type="courseware",
    )

    assert decision["should_generate"] is True
    assert "conceptual_depth_readiness" in decision["support_reasons"]
    assert "structure_need" not in decision["support_reasons"]


@pytest.mark.asyncio
async def test_learning_visual_retries_failed_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = 0

    async def fail_once(*args: object, **kwargs: object) -> list[tuple[bytes, str]]:
        nonlocal attempts
        attempts += 1
        raise ValueError(f"provider unavailable {attempts}")

    monkeypatch.setattr(visuals, "generate_image", fail_once)

    trace = await visuals.generate_learning_visual(
        {
            "kind": "quiz",
            "title": "搜索引擎营销",
            "items": [{"question": "关键词连接哪两类内容？"}],
            "visual_targets": [{"label": "关键词"}],
            "slr_visual_reason": "监控与调节",
        },
        generation_id="retry-test",
        max_attempts=2,
    )

    assert attempts == 2
    assert trace["status"] == "failed"
    assert len(trace["attempts"]) == 2
    assert trace["attempts"][0]["status"] == "failed"
