from __future__ import annotations

import pytest

from traittutor.generate.document_material import LearningDocumentError, prepare_learning_document


def _sample_pdf_bytes() -> bytes:
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "TraitTutor sample PDF material")
    data = doc.tobytes(garbage=4, deflate=True)
    doc.close()
    return data


def test_prepare_pdf_extracts_pages_without_converting() -> None:
    prepared = prepare_learning_document(
        "lesson.pdf",
        _sample_pdf_bytes(),
        mime_type="application/pdf",
    )

    assert prepared["filename"] == "lesson.pdf"
    assert prepared["converted_to_pdf"] is False
    assert prepared["mime_type"] == "application/pdf"
    assert prepared["page_count"] == 1
    assert "TraitTutor sample PDF material" in prepared["page_slices"][0]["text"]


def test_prepare_pdf_rejects_extension_spoofing_before_conversion() -> None:
    with pytest.raises(LearningDocumentError, match="not a valid PDF"):
        prepare_learning_document(
            "lesson.pdf",
            b"PK\x03\x04this is actually a zip based office file",
            mime_type="application/pdf",
        )


def test_prepare_material_rejects_mime_extension_mismatch() -> None:
    with pytest.raises(LearningDocumentError, match="declared file type"):
        prepare_learning_document(
            "lesson.pdf",
            _sample_pdf_bytes(),
            mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
