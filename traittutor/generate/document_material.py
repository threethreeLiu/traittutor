"""Normalize uploaded learning documents to PDF page slices.

The generation model never receives a binary Office document.  Office files
are converted to PDF in an isolated temporary directory, then the resulting
PDF is extracted one page at a time.  Page numbers survive through
``MaterialResolver`` as source locators, making generated material traceable.
"""

from __future__ import annotations

import io
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any

import fitz

from traittutor.utils.document_extractor import DocumentExtractionError, extract_text_from_bytes
from traittutor.utils.document_validator import DocumentValidator


PDF_CONVERTIBLE_EXTENSIONS = frozenset({
    ".pdf", ".doc", ".docx", ".rtf", ".odt", ".xls", ".xlsx", ".ods",
    ".ppt", ".pptx", ".odp", ".txt", ".md", ".csv", ".html", ".htm",
})
MAX_PAGE_CHARS = 12_000


class LearningDocumentError(ValueError):
    """A safe, learner-facing document preparation error."""


def prepare_learning_document(filename: str, data: bytes) -> dict[str, Any]:
    """Validate, convert, and return page-addressable text slices.

    LibreOffice is used for Office fidelity where available.  For text-like
    files, a small PDF is generated locally from already-supported extraction
    output, so every accepted input follows the same PDF-page pipeline.
    """
    safe_name = DocumentValidator.validate_upload_safety(filename, len(data))
    ext = Path(safe_name).suffix.lower()
    if ext not in PDF_CONVERTIBLE_EXTENSIONS:
        raise LearningDocumentError("Only PDF, Word, spreadsheet, presentation, and text materials are supported")
    if ext == ".pdf":
        pdf_bytes = data
        converted = False
    elif ext in {".txt", ".md", ".csv", ".html", ".htm"}:
        try:
            pdf_bytes = _text_pdf(extract_text_from_bytes(safe_name, data))
        except DocumentExtractionError as exc:
            raise LearningDocumentError(str(exc)) from exc
        converted = True
    else:
        pdf_bytes = _convert_office_to_pdf(safe_name, data)
        converted = True
    pages = _pdf_page_slices(pdf_bytes, safe_name)
    if not pages:
        raise LearningDocumentError(f"{safe_name} has no extractable text")
    return {
        "filename": safe_name,
        "converted_to_pdf": converted,
        "page_count": len(pages),
        "page_slices": pages,
    }


def _convert_office_to_pdf(filename: str, data: bytes) -> bytes:
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        raise LearningDocumentError("Office-to-PDF conversion is unavailable on this server")
    with tempfile.TemporaryDirectory(prefix="traittutor-material-") as tmp:
        workdir = Path(tmp)
        source = workdir / filename
        source.write_bytes(data)
        # LibreOffice needs a writable, isolated profile in server processes;
        # reusing a desktop profile can make headless conversion refuse to
        # start or contend with an interactive instance.
        profile_dir = workdir / "lo-profile"
        profile_dir.mkdir()
        try:
            completed = subprocess.run(
                [
                    soffice, "--headless", f"-env:UserInstallation={profile_dir.as_uri()}",
                    "--convert-to", "pdf", "--outdir", str(workdir), str(source),
                ],
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=45,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise LearningDocumentError(f"Could not convert {filename} to PDF") from exc
        converted = workdir / f"{source.stem}.pdf"
        if completed.returncode != 0 or not converted.is_file():
            raise LearningDocumentError(f"Could not convert {filename} to PDF")
        return converted.read_bytes()


def _text_pdf(text: str) -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    rect = page.rect + (36, 36, -36, -36)
    page.insert_textbox(rect, text, fontsize=10, fontname="helv")
    result = doc.tobytes(garbage=4, deflate=True)
    doc.close()
    return result


def _pdf_page_slices(data: bytes, filename: str) -> list[dict[str, Any]]:
    try:
        document = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise LearningDocumentError(f"{filename} is not a readable PDF") from exc
    try:
        if document.is_encrypted and not document.authenticate(""):
            raise LearningDocumentError(f"{filename} is encrypted and cannot be read")
        return [
            {"page": index, "text": (page.get_text("text") or "").strip()[:MAX_PAGE_CHARS]}
            for index, page in enumerate(document, start=1)
            if (page.get_text("text") or "").strip()
        ]
    finally:
        document.close()
