from __future__ import annotations

import pytest

from traittutor.services.prompt.markdown import nested_prompt_text


@pytest.mark.parametrize(
    ("prompts", "path", "expected"),
    [
        ({"solve": {"system": "custom"}}, ("solve", "system"), "custom"),
        ({"solve": {"system": ""}}, ("solve", "system"), ""),
        ({"solve": {"system": 1}}, ("solve", "system"), ""),
        ({"solve": "not-a-mapping"}, ("solve", "system"), ""),
        ({}, ("solve", "system"), ""),
    ],
)
def test_nested_prompt_text_preserves_capability_lookup_behavior(
    prompts: dict[str, object],
    path: tuple[str, ...],
    expected: str,
) -> None:
    assert nested_prompt_text(prompts, path) == expected


@pytest.mark.parametrize(
    ("prompts", "expected"),
    [
        ({"notebook": {"system": "custom"}}, "custom"),
        ({"notebook": {"system": ""}}, ""),
        ({"notebook": {"system": 1}}, "fallback"),
        ({"notebook": "not-a-mapping"}, "fallback"),
        ({}, "fallback"),
    ],
)
def test_nested_prompt_text_uses_default_only_for_missing_or_invalid_values(
    prompts: dict[str, object], expected: str
) -> None:
    assert nested_prompt_text(prompts, ("notebook", "system"), "fallback") == expected
