"""WS-2 (PRD F-13 / G2): material-language detection + output-language priority.

These lock the invariant that the generation chain never silently defaults to
Chinese: when no language signal exists it degrades explicitly to ``"und"``
instead of assuming ``zh``. The ``en``-material / ``zh``-UI case must follow the
material (``en``), not the UI.
"""

from __future__ import annotations

import pytest

from traittutor.generate.material_analysis import (
    detect_material_language,
    normalize_language_tag,
)
from traittutor.generate.service import _resolve_output_language


class TestNormalizeLanguageTag:
    @pytest.mark.parametrize(
        "value, expected",
        [
            ("zh", "zh-CN"),
            ("ZH-CN", "zh-CN"),
            ("zh-tw", "zh-CN"),
            ("en", "en"),
            ("en-US", "en"),
            ("fr", "fr"),
            ("ja-JP", "ja-jp"),
            ("", None),
            ("   ", None),
            ("???", None),  # not a BCP-47 tag
            ("zhgarbage", None),  # adversarial prefix must NOT map to zh-CN
            ("english", None),  # adversarial prefix must NOT map to en
            ("z", None),  # too short to be a language tag
            (None, None),
            (123, None),  # type: ignore[arg-type]
        ],
    )
    def test_canonicalization(self, value, expected):
        assert normalize_language_tag(value) == expected


class TestDetectMaterialLanguage:
    def test_cjk_text_detected_as_zh(self):
        chunks = [{"text": "今天我们学习二次函数与一元二次方程的图像关系。"}]
        tag, conf = detect_material_language(chunks)
        assert tag == "zh-CN"
        assert conf is not None and 0 < conf <= 1

    def test_latin_text_detected_as_en(self):
        chunks = [{"text": "Today we study quadratic functions and their graphs in detail."}]
        tag, _conf = detect_material_language(chunks)
        assert tag == "en"

    def test_uses_excerpt_when_no_text(self):
        chunks = [{"excerpt": "Quadratic equations appear across algebra topics here."}]
        assert detect_material_language(chunks)[0] == "en"

    def test_too_little_signal_returns_none(self):
        # < 12 scoring codepoints -> undetermined, never a silent language default.
        assert detect_material_language([{"text": "hi"}]) == (None, None)

    def test_empty_chunks_returns_none(self):
        assert detect_material_language([]) == (None, None)

    def test_material_heuristic_overrides_conflicting_hint(self):
        # Strong evidence in the actual input wins over model/UI metadata.
        chunks = [{"text": "Today we study quadratic functions and their graphs."}]
        assert detect_material_language(chunks, hint="zh") == ("en", 1.0)

    def test_hint_is_used_when_material_signal_is_too_short(self):
        assert detect_material_language([{"text": "π"}], hint="el") == ("el", None)

    def test_invalid_hint_falls_back_to_heuristic(self):
        chunks = [{"text": "Today we study quadratic functions and their graphs."}]
        assert detect_material_language(chunks, hint="???")[0] == "en"


class TestResolveOutputLanguage:
    """G2 invariant: material language > UI hint > explicit degrade."""

    def test_material_language_wins_over_explicit_ui_hint(self):
        assert _resolve_output_language({"language": "en"}, "zh-CN") == "zh-CN"

    def test_material_language_used_when_no_explicit(self):
        # English material + Chinese UI -> output follows the material (en).
        assert _resolve_output_language({}, "en") == "en"

    def test_never_silently_defaults_to_chinese(self):
        # Core G2 fix: no signal -> "und" (explicit degrade), NEVER zh-CN.
        resolved = _resolve_output_language({}, None)
        assert resolved == "und"
        assert resolved != "zh-CN"

    def test_invalid_explicit_falls_through_to_material(self):
        assert _resolve_output_language({"language": "???"}, "fr") == "fr"

    def test_invalid_everywhere_degrades(self):
        assert _resolve_output_language({"language": "???"}, None) == "und"
