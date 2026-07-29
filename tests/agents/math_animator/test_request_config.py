from __future__ import annotations

import pytest

from traittutor.agents.math_animator.request_config import (
    validate_math_animator_request_config,
)


def test_validate_math_animator_request_config_defaults() -> None:
    config = validate_math_animator_request_config(None)
    assert config.output_mode == "video"
    assert config.quality == "medium"
    assert config.style_hint == ""


def test_validate_math_animator_request_config_rejects_unknown_fields() -> None:
    with pytest.raises(ValueError, match="Invalid math animator config"):
        validate_math_animator_request_config(
            {
                "output_mode": "image",
                "quality": "high",
                "unexpected": True,
            }
        )
