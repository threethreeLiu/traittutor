from __future__ import annotations

import pytest

from traittutor.runtime.request_contracts import (
    get_capability_request_schema,
    validate_visualize_request_config,
)
from traittutor.services.config import capabilities_settings


def test_visualize_request_contract_is_registered_and_fail_closed() -> None:
    config = validate_visualize_request_config(
        {"render_mode": "manim_video", "quality": "high", "style_hint": "chalkboard"}
    )

    assert config.render_mode == "manim_video"
    assert config.quality == "high"
    assert get_capability_request_schema("visualize")["properties"]["render_mode"]

    with pytest.raises(ValueError, match="Invalid visualize config"):
        validate_visualize_request_config({"remote_component": "https://example.com/widget.js"})


def test_solve_params_read_live_llm_and_loop_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    class _ConfigManager:
        def load_config(self) -> dict[str, object]:
            return {"capabilities": {"solve": {"max_rounds": 17, "max_replans": 4}}}

    monkeypatch.setattr(
        capabilities_settings,
        "_read_agents_yaml",
        lambda: {"capabilities": {"solve": {"temperature": 0.6, "max_tokens": 4096}}},
    )
    monkeypatch.setattr(capabilities_settings, "ConfigManager", _ConfigManager)

    assert capabilities_settings.get_solve_params() == {
        "temperature": 0.6,
        "max_tokens": 4096,
        "max_rounds": 17,
        "max_replans": 4,
    }
