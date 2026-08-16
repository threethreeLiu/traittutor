"""Normalize uploaded learning documents to PDF page slices.

The generation model never receives a binary Office document.  Office files
are converted to PDF in an isolated temporary directory, then the resulting
PDF is extracted one page at a time.  Page numbers survive through
``MaterialResolver`` as source locators, making generated material traceable.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any

import fitz

from traittutor.utils.document_extractor import DocumentExtractionError, extract_text_from_bytes
from traittutor.utils.document_validator import DocumentValidator

PDF_CONVERTIBLE_EXTENSIONS = frozenset(
    {
        ".pdf",
        ".doc",
        ".docx",
        ".rtf",
        ".odt",
        ".xls",
        ".xlsx",
        ".ods",
        ".ppt",
        ".pptx",
        ".odp",
        ".txt",
        ".md",
        ".csv",
        ".html",
        ".htm",
    }
)
MAX_PAGE_CHARS = 12_000
_IGNORED_BROWSER_MIME_TYPES = {"", "application/octet-stream", "binary/octet-stream"}
_EXTENSION_MIME_TYPES: dict[str, set[str]] = {
    ".pdf": {"application/pdf"},
    ".doc": {"application/msword", "application/octet-stream"},
    ".docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
    ".rtf": {"application/rtf", "text/rtf"},
    ".odt": {"application/vnd.oasis.opendocument.text"},
    ".xls": {"application/vnd.ms-excel", "application/octet-stream"},
    ".xlsx": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
    ".ods": {"application/vnd.oasis.opendocument.spreadsheet"},
    ".ppt": {"application/vnd.ms-powerpoint", "application/octet-stream"},
    ".pptx": {"application/vnd.openxmlformats-officedocument.presentationml.presentation"},
    ".odp": {"application/vnd.oasis.opendocument.presentation"},
    ".txt": {"text/plain"},
    ".md": {"text/markdown", "text/plain"},
    ".csv": {"text/csv", "application/csv", "application/vnd.ms-excel", "text/plain"},
    ".html": {"text/html"},
    ".htm": {"text/html"},
}
_ZIP_BASED_EXTENSIONS = {".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp"}
_OLE_BASED_EXTENSIONS = {".doc", ".xls", ".ppt"}


class LearningDocumentError(ValueError):
    """A safe, learner-facing document preparation error."""


def prepare_learning_document(
    filename: str, data: bytes, mime_type: str | None = None
) -> dict[str, Any]:
    """Validate, convert, and return page-addressable text slices.

    LibreOffice is used for Office fidelity where available.  For text-like
    files, a small PDF is generated locally from already-supported extraction
    output, so every accepted input follows the same PDF-page pipeline.
    """
    if not data:
        raise LearningDocumentError("Uploaded file is empty")
    safe_name = DocumentValidator.validate_upload_safety(filename, len(data))
    ext = Path(safe_name).suffix.lower()
    if ext not in PDF_CONVERTIBLE_EXTENSIONS:
        raise LearningDocumentError(
            "Only PDF, Word, spreadsheet, presentation, and text materials are supported"
        )
    _validate_declared_type(safe_name, data, mime_type)
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
        "mime_type": _normalized_mime_type(mime_type) or mimetypes.guess_type(safe_name)[0] or "",
        "page_count": len(pages),
        "page_slices": pages,
    }


def _normalized_mime_type(mime_type: str | None) -> str:
    return str(mime_type or "").split(";", 1)[0].strip().lower()


def _validate_declared_type(filename: str, data: bytes, mime_type: str | None) -> None:
    ext = Path(filename).suffix.lower()
    normalized_mime = _normalized_mime_type(mime_type)
    allowed_mimes = _EXTENSION_MIME_TYPES.get(ext, set())
    if (
        normalized_mime not in _IGNORED_BROWSER_MIME_TYPES
        and allowed_mimes
        and normalized_mime not in allowed_mimes
    ):
        raise LearningDocumentError(f"{filename} does not match its declared file type")

    if ext == ".pdf":
        if not data.startswith(b"%PDF-"):
            raise LearningDocumentError(f"{filename} is not a valid PDF file")
        return
    if ext in _ZIP_BASED_EXTENSIONS and not data.startswith(
        (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
    ):
        raise LearningDocumentError(
            f"{filename} does not look like a valid Office/OpenDocument file"
        )
    if ext in _OLE_BASED_EXTENSIONS and not data.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        raise LearningDocumentError(f"{filename} does not look like a valid legacy Office file")


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
                    soffice,
                    "--headless",
                    f"-env:UserInstallation={profile_dir.as_uri()}",
                    "--convert-to",
                    "pdf",
                    "--outdir",
                    str(workdir),
                    str(source),
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
