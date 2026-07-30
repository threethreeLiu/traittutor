from __future__ import annotations

from types import SimpleNamespace

from traittutor.generate.material_abstraction import (
    build_learning_targets,
    build_material_abstraction,
    subject_ref_from_analysis,
)


def _chunk(chunk_id: str = "upload-1") -> SimpleNamespace:
    return SimpleNamespace(
        to_dict=lambda: {
            "chunk_id": chunk_id,
            "source_type": "upload",
            "source_id": "material-1",
            "title": "函数.pdf",
            "text": "一次函数的斜率表示函数图像的变化率。",
            "excerpt": "一次函数的斜率",
            "citation": {
                "source_type": "upload",
                "source_id": "material-1",
                "title": "函数.pdf",
                "locator": {"page": 2, "chunk_index": 1},
            },
        }
    )


def test_material_abstraction_reuses_existing_analysis_and_preserves_file_metadata():
    analysis = {
        "subject": "mathematics",
        "sub_subject": "linear functions",
        "chinese_grade": "junior_2",
        "international_grade": "grade_8",
        "difficulty": "standard",
        "confidence": 0.86,
    }
    resolved = SimpleNamespace(source_type="upload", source_id="material-1", title="函数.pdf", chunks=(_chunk(),))

    abstraction = build_material_abstraction(
        resolved=resolved,
        analysis=analysis,
        original_metadata={
            "filename": "函数.pdf",
            "converted_to_pdf": True,
            "page_count": 12,
            "page_slices": [{"page": 2, "text": "大量正文不应进入摘要"}],
        },
    )

    assert abstraction.subject_ref
    assert abstraction.subject_ref["subject_id"] == "mathematics"
    assert abstraction.analysis == analysis
    assert abstraction.file_metadata["filename"] == "函数.pdf"
    assert abstraction.file_metadata["page_count"] == 12
    assert abstraction.file_metadata["page_slice_count"] == 1
    assert "page_slices" not in abstraction.file_metadata
    assert abstraction.source_refs[0]["locator"]["page"] == 2
    assert abstraction.concept_candidates[0]["temporary"] is True


def test_subject_ref_from_analysis_is_none_without_existing_analysis_subject():
    assert subject_ref_from_analysis(None) is None
    assert subject_ref_from_analysis({"confidence": 0.9}) is None


def test_learning_targets_are_generation_guides_not_bkt_updates():
    resolved = SimpleNamespace(source_type="upload", source_id="material-1", title="函数.pdf", chunks=(_chunk("chunk-2"),))
    abstraction = build_material_abstraction(
        resolved=resolved,
        analysis={"subject": "mathematics", "sub_subject": "linear functions", "confidence": 0.8},
        original_metadata={},
    )

    targets = build_learning_targets(
        generation_type="quiz",
        abstraction=abstraction,
        personalization_context={
            "relevant_concept_signals": [
                {
                    "concept_id": "mathematics.linear-functions.slope",
                    "label": "斜率",
                    "support_level": "needs_support",
                    "mastery_probability": 0.32,
                    "evidence_refs": ["question:old"],
                }
            ]
        },
    )

    assert targets["subject_ref"]["subject_id"] == "mathematics"
    assert targets["quiz_targets"][0]["concept_id"] == "mathematics.linear-functions.slope"
    assert targets["quiz_targets"][0]["priority"] == "review"
    assert "BKT changes require later learner events" in targets["boundary"]
