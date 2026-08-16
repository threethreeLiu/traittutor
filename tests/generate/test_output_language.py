from __future__ import annotations

from traittutor.generate.material_analysis import detect_material_language
from traittutor.generate.service import _resolve_output_language


def test_material_text_language_overrides_conflicting_model_hint() -> None:
    chunks = [{"text": "这是一段关于线性代数和矩阵变换的大学课程学习材料。"}]

    language, confidence = detect_material_language(chunks, hint="en")

    assert language == "zh-CN"
    assert confidence is not None


def test_model_hint_remains_fallback_for_short_or_unclassified_input() -> None:
    language, confidence = detect_material_language([{"text": "π"}], hint="el")

    assert language == "el"
    assert confidence is None


def test_generated_output_uses_material_language_before_ui_language() -> None:
    assert _resolve_output_language({"language": "en"}, "zh-CN") == "zh-CN"
    assert _resolve_output_language({"language": "en"}, None) == "en"
    assert _resolve_output_language({}, None) == "und"
