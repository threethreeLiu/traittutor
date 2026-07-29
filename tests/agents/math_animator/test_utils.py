from __future__ import annotations

from traittutor.agents.math_animator.utils import build_repair_error_message, extract_json_object


def test_build_repair_error_message_adds_targeted_3d_point_hint() -> None:
    error_message = (
        "vectorized_mobject.py:855 in append_points\n"
        "ValueError: could not broadcast input array from shape (1,2) into shape (1,3)"
    )

    enriched = build_repair_error_message(error_message)

    assert "Targeted repair hints" in enriched
    assert "[x, y, 0]" in enriched
    assert "axes.c2p" in enriched


def test_build_repair_error_message_keeps_unknown_errors_plain() -> None:
    error_message = "NameError: name 'FadeInn' is not defined"
    assert build_repair_error_message(error_message) == error_message


def test_extract_json_object_accepts_trailing_extra_data() -> None:
    raw = (
        '{"code":"from manim import *\\nclass A(Scene):\\n    pass","rationale":"fix syntax"}\n'
        "```python\n# extra trailing block from model\n```"
    )

    parsed = extract_json_object(raw)

    assert parsed["rationale"] == "fix syntax"
    assert "class A(Scene)" in parsed["code"]


def test_extract_json_object_accepts_prefixed_text_before_json() -> None:
    raw = (
        "Here is the repaired JSON:\n"
        '{"code":"from manim import *\\nclass B(Scene):\\n    pass","rationale":"repair"}'
    )

    parsed = extract_json_object(raw)

    assert parsed["rationale"] == "repair"
    assert "class B(Scene)" in parsed["code"]
