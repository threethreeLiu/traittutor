"""Resolve TraitTutor generation inputs into traceable material chunks.

The generator consumes the same persisted sources that users already see in
Knowledge, Notebook, and chat attachments.  This module owns only the
read-side normalization step; generation, storage, and API streaming remain
separate concerns.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from hashlib import sha256
from pathlib import Path
import re
from typing import Any, Literal

from traittutor.utils.document_extractor import (
    DocumentExtractionError,
    extract_text_from_path,
    is_document_extension,
)

MaterialSourceType = Literal["knowledge", "notebook", "upload", "paste"]

DEFAULT_CHUNK_CHARS = 1_200
DEFAULT_EXCERPT_CHARS = 280
_SUPPORTED_SOURCE_TYPES = frozenset({"knowledge", "notebook", "upload", "paste"})


class MaterialResolutionError(ValueError):
    """Raised when a selected material cannot be resolved safely."""


@dataclass(frozen=True)
class MaterialReference:
    """A request to resolve one of the supported material sources.

    ``source_id`` is a knowledge-base reference, notebook id, or attachment
    id depending on ``source_type``.  ``metadata`` carries the minimal
    locator specific to the existing source service:

    * knowledge: ``file_path`` or ``file_paths`` relative to ``raw/``;
    * notebook: optional ``record_id`` or ``record_ids``;
    * upload: ``session_id`` and ``filename`` (plus optional
      ``extracted_text`` persisted by the chat attachment pipeline).
    """

    source_type: MaterialSourceType
    source_id: str | None = None
    title: str = ""
    text: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MaterialCitation:
    """A source locator attached to every resolved chunk."""

    source_type: MaterialSourceType
    source_id: str
    title: str
    locator: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MaterialChunk:
    """One deterministic, traceable section of a resolved material source."""

    chunk_id: str
    source_type: MaterialSourceType
    source_id: str
    title: str
    text: str
    excerpt: str
    citation: MaterialCitation

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "title": self.title,
            "text": self.text,
            "excerpt": self.excerpt,
            "citation": self.citation.to_dict(),
        }


@dataclass(frozen=True)
class ResolvedMaterial:
    """Normalized material and its source-grounded chunks."""

    source_type: MaterialSourceType
    source_id: str
    title: str
    chunks: tuple[MaterialChunk, ...]

    @property
    def char_count(self) -> int:
        return sum(len(chunk.text) for chunk in self.chunks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_type": self.source_type,
            "source_id": self.source_id,
            "title": self.title,
            "char_count": self.char_count,
            "chunks": [chunk.to_dict() for chunk in self.chunks],
        }


class MaterialResolver:
    """Resolve product-owned material sources through their existing services."""

    def __init__(
        self,
        *,
        knowledge_manager: Any | None = None,
        notebook_manager: Any | None = None,
        attachment_store: Any | None = None,
        chunk_chars: int = DEFAULT_CHUNK_CHARS,
        excerpt_chars: int = DEFAULT_EXCERPT_CHARS,
    ) -> None:
        if chunk_chars < 128:
            raise ValueError("chunk_chars must be at least 128")
        if excerpt_chars < 32:
            raise ValueError("excerpt_chars must be at least 32")
        self._knowledge_manager = knowledge_manager
        self._notebook_manager = notebook_manager
        self._attachment_store = attachment_store
        self._chunk_chars = chunk_chars
        self._excerpt_chars = excerpt_chars

    def resolve(self, material: MaterialReference | Mapping[str, Any] | Any) -> ResolvedMaterial:
        """Resolve ``material`` without duplicating Knowledge/Notebook storage logic.

        In addition to :class:`MaterialReference`, existing request dataclasses
        with the same public fields are accepted so the future runner can pass
        its ``MaterialSource`` object directly without an adapter layer.
        """
        reference = _coerce_reference(material)
        if reference.source_type == "paste":
            return self._resolve_paste(reference)
        if reference.source_type == "knowledge":
            return self._resolve_knowledge(reference)
        if reference.source_type == "notebook":
            return self._resolve_notebook(reference)
        if reference.source_type == "upload":
            return self._resolve_upload(reference)
        raise MaterialResolutionError(f"unsupported material source: {reference.source_type}")

    def _resolve_paste(self, reference: MaterialReference) -> ResolvedMaterial:
        text = _normalized_text(reference.text)
        if not text:
            raise MaterialResolutionError("Paste material is empty")
        source_id = _required_or_generated_id(
            reference.source_id,
            prefix="paste",
            content=text,
        )
        title = _title_or_default(reference.title, "Pasted material")
        chunks = self._chunks_for_text(
            source_type="paste",
            source_id=source_id,
            title=title,
            text=text,
            locator={"kind": "pasted_text"},
        )
        return ResolvedMaterial("paste", source_id, title, chunks)

    def _resolve_knowledge(self, reference: MaterialReference) -> ResolvedMaterial:
        kb_ref = _required_metadata_or_source_id(reference, "knowledge_base_id")
        manager, kb_name = self._knowledge_manager_for(kb_ref)
        try:
            raw_dir = manager.get_knowledge_base_path(kb_name) / "raw"
        except (OSError, ValueError) as exc:
            raise MaterialResolutionError(f"Knowledge material not found: {kb_ref}") from exc
        if not raw_dir.is_dir():
            raise MaterialResolutionError(
                f"Knowledge base '{kb_ref}' has no raw material directory"
            )

        paths = _knowledge_paths(raw_dir, reference.metadata)
        chunks: list[MaterialChunk] = []
        for path in paths:
            relative_path = path.relative_to(raw_dir.resolve()).as_posix()
            try:
                text = _normalized_text(extract_text_from_path(path))
            except (DocumentExtractionError, OSError) as exc:
                raise MaterialResolutionError(
                    f"Could not extract knowledge material '{relative_path}': {exc}"
                ) from exc
            if not text:
                continue
            source_id = f"{kb_ref}:{relative_path}"
            chunks.extend(
                self._chunks_for_text(
                    source_type="knowledge",
                    source_id=source_id,
                    title=path.name,
                    text=text,
                    locator={
                        "knowledge_base_id": kb_ref,
                        "knowledge_base_name": kb_name,
                        "path": relative_path,
                    },
                )
            )

        if not chunks:
            raise MaterialResolutionError("Selected knowledge material has no extractable text")
        title = _title_or_default(reference.title, kb_name)
        return ResolvedMaterial("knowledge", kb_ref, title, tuple(chunks))

    def _resolve_notebook(self, reference: MaterialReference) -> ResolvedMaterial:
        notebook_id = _required_metadata_or_source_id(reference, "notebook_id")
        manager = self._get_notebook_manager()
        record_ids = _metadata_strings(reference.metadata, "record_ids", "record_id")
        records = manager.get_records(notebook_id, record_ids or None)
        if not records:
            raise MaterialResolutionError(f"Notebook material not found: {notebook_id}")

        chunks: list[MaterialChunk] = []
        for record in records:
            record_id = str(record.get("id") or "").strip()
            text = _normalized_text(str(record.get("output") or ""))
            if not record_id or not text:
                continue
            record_title = _title_or_default(str(record.get("title") or ""), "Notebook record")
            source_id = f"{notebook_id}:{record_id}"
            chunks.extend(
                self._chunks_for_text(
                    source_type="notebook",
                    source_id=source_id,
                    title=record_title,
                    text=text,
                    locator={
                        "notebook_id": notebook_id,
                        "record_id": record_id,
                        "record_type": str(record.get("type") or ""),
                    },
                )
            )

        if not chunks:
            raise MaterialResolutionError("Selected notebook records have no usable output")
        notebook = manager.get_notebook(notebook_id) or {}
        title = _title_or_default(reference.title, str(notebook.get("name") or "Notebook"))
        return ResolvedMaterial("notebook", notebook_id, title, tuple(chunks))

    def _resolve_upload(self, reference: MaterialReference) -> ResolvedMaterial:
        attachment_id = _required_metadata_or_source_id(reference, "attachment_id")
        metadata = reference.metadata
        filename = str(metadata.get("filename") or reference.title or "Uploaded file").strip()
        session_id = str(metadata.get("session_id") or "").strip()
        text = _normalized_text(str(metadata.get("extracted_text") or reference.text or ""))

        page_slices = metadata.get("page_slices")
        if isinstance(page_slices, Sequence) and not isinstance(
            page_slices, (str, bytes, bytearray)
        ):
            page_chunks: list[MaterialChunk] = []
            for page in page_slices:
                if not isinstance(page, Mapping):
                    continue
                page_number = int(page.get("page") or 0)
                page_text = _normalized_text(str(page.get("text") or ""))
                if page_number < 1 or not page_text:
                    continue
                page_chunks.extend(
                    self._chunks_for_text(
                        source_type="upload",
                        source_id=attachment_id,
                        title=filename,
                        text=page_text,
                        locator={
                            "session_id": session_id,
                            "attachment_id": attachment_id,
                            "filename": filename,
                            "page": page_number,
                            "converted_to_pdf": bool(metadata.get("converted_to_pdf")),
                        },
                    )
                )
            if page_chunks:
                return ResolvedMaterial(
                    "upload",
                    attachment_id,
                    _title_or_default(reference.title, filename),
                    tuple(page_chunks),
                )

        if not text:
            if not session_id:
                raise MaterialResolutionError("Upload material requires metadata.session_id")
            target = self._get_attachment_store().resolve_path(
                session_id=session_id,
                attachment_id=attachment_id,
                filename=filename,
            )
            if target is None:
                raise MaterialResolutionError(f"Upload attachment not found: {attachment_id}")
            try:
                text = _normalized_text(extract_text_from_path(target))
            except (DocumentExtractionError, OSError) as exc:
                raise MaterialResolutionError(
                    f"Could not extract upload attachment '{filename}': {exc}"
                ) from exc

        if not text:
            raise MaterialResolutionError("Upload material has no extractable text")
        title = _title_or_default(reference.title, filename)
        chunks = self._chunks_for_text(
            source_type="upload",
            source_id=attachment_id,
            title=title,
            text=text,
            locator={
                "session_id": session_id,
                "attachment_id": attachment_id,
                "filename": filename,
                "url": str(metadata.get("url") or ""),
            },
        )
        return ResolvedMaterial("upload", attachment_id, title, chunks)

    def _knowledge_manager_for(self, kb_ref: str) -> tuple[Any, str]:
        if self._knowledge_manager is not None:
            return self._knowledge_manager, kb_ref
        try:
            from traittutor.multi_user.knowledge_access import manager_for_resource, resolve_kb

            resource = resolve_kb(kb_ref)
            return manager_for_resource(resource), resource.name
        except Exception as exc:
            raise MaterialResolutionError(f"Knowledge material not found: {kb_ref}") from exc

    def _get_notebook_manager(self) -> Any:
        if self._notebook_manager is not None:
            return self._notebook_manager
        from traittutor.services.notebook import get_notebook_manager

        return get_notebook_manager()

    def _get_attachment_store(self) -> Any:
        if self._attachment_store is not None:
            return self._attachment_store
        from traittutor.services.storage import get_attachment_store

        return get_attachment_store()

    def _chunks_for_text(
        self,
        *,
        source_type: MaterialSourceType,
        source_id: str,
        title: str,
        text: str,
        locator: Mapping[str, Any],
    ) -> tuple[MaterialChunk, ...]:
        chunks: list[MaterialChunk] = []
        for index, (start, end, chunk_text) in enumerate(
            _split_text(text, self._chunk_chars),
            start=1,
        ):
            chunk_locator = {
                **dict(locator),
                "chunk_index": index,
                "char_start": start,
                "char_end": end,
            }
            citation = MaterialCitation(
                source_type=source_type,
                source_id=source_id,
                title=title,
                locator=chunk_locator,
            )
            chunks.append(
                MaterialChunk(
                    chunk_id=_chunk_id(source_type, source_id, chunk_locator, chunk_text),
                    source_type=source_type,
                    source_id=source_id,
                    title=title,
                    text=chunk_text,
                    excerpt=_excerpt(chunk_text, self._excerpt_chars),
                    citation=citation,
                )
            )
        return tuple(chunks)


def _coerce_reference(material: MaterialReference | Mapping[str, Any] | Any) -> MaterialReference:
    if isinstance(material, MaterialReference):
        values: Mapping[str, Any] = {
            "source_type": material.source_type,
            "source_id": material.source_id,
            "title": material.title,
            "text": material.text,
            "metadata": material.metadata,
        }
    elif isinstance(material, Mapping):
        values = material
    else:
        values = {
            "source_type": getattr(material, "source_type", ""),
            "source_id": getattr(material, "source_id", None),
            "title": getattr(material, "title", ""),
            "text": getattr(material, "text", ""),
            "metadata": getattr(material, "metadata", {}),
        }
    raw_type = str(values.get("source_type") or "").strip().lower()
    if raw_type not in _SUPPORTED_SOURCE_TYPES:
        raise MaterialResolutionError(f"unsupported material source: {raw_type or 'missing'}")
    metadata = values.get("metadata") or {}
    if not isinstance(metadata, Mapping):
        raise MaterialResolutionError("material metadata must be an object")
    return MaterialReference(
        source_type=raw_type,  # type: ignore[arg-type]
        source_id=_optional_string(values.get("source_id")),
        title=str(values.get("title") or ""),
        text=str(values.get("text") or ""),
        metadata=dict(metadata),
    )


def _required_metadata_or_source_id(reference: MaterialReference, metadata_key: str) -> str:
    value = reference.metadata.get(metadata_key) or reference.source_id
    normalized = _optional_string(value)
    if normalized:
        return normalized
    raise MaterialResolutionError(f"{reference.source_type} material requires source_id")


def _required_or_generated_id(
    explicit_value: str | None,
    *,
    prefix: str,
    content: str,
) -> str:
    """Return an explicit id or a deterministic fallback from source content."""
    explicit = _optional_string(explicit_value)
    if explicit:
        return explicit
    digest = sha256(content.encode("utf-8")).hexdigest()[:20]
    return f"{prefix}-{digest}"


def _optional_string(value: Any) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _title_or_default(value: str, default: str) -> str:
    return str(value or "").strip() or default


def _metadata_strings(metadata: Mapping[str, Any], *keys: str) -> list[str]:
    values: list[str] = []
    for key in keys:
        raw = metadata.get(key)
        if isinstance(raw, str):
            values.append(raw)
        elif isinstance(raw, Sequence) and not isinstance(raw, (bytes, bytearray)):
            values.extend(str(item) for item in raw)
    return [item.strip() for item in values if item and item.strip()]


def _knowledge_paths(raw_dir: Path, metadata: Mapping[str, Any]) -> list[Path]:
    raw_root = raw_dir.resolve()
    requested = _metadata_strings(metadata, "file_paths", "file_path", "path")
    if requested:
        paths = [_resolve_raw_path(raw_root, item) for item in requested]
    else:
        paths = sorted(
            (
                path.resolve()
                for path in raw_root.rglob("*")
                if path.is_file() and is_document_extension(path.name)
            ),
            key=lambda path: path.as_posix().lower(),
        )
    readable = [path for path in paths if is_document_extension(path.name)]
    if not readable:
        raise MaterialResolutionError("Selected knowledge material has no supported documents")
    return readable


def _resolve_raw_path(raw_root: Path, relative_path: str) -> Path:
    target = (raw_root / relative_path).resolve()
    try:
        target.relative_to(raw_root)
    except ValueError as exc:
        raise MaterialResolutionError(
            "Knowledge file path must stay inside the selected knowledge base"
        ) from exc
    if not target.is_file():
        raise MaterialResolutionError(f"Knowledge file not found: {relative_path}")
    return target


def _normalized_text(text: str) -> str:
    return re.sub(
        r"\n{3,}", "\n\n", str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    ).strip()


def _split_text(text: str, chunk_chars: int) -> list[tuple[int, int, str]]:
    chunks: list[tuple[int, int, str]] = []
    position = 0
    length = len(text)
    while position < length:
        while position < length and text[position].isspace():
            position += 1
        if position >= length:
            break
        limit = min(position + chunk_chars, length)
        end = limit
        if limit < length:
            search_start = position + max(chunk_chars // 2, 1)
            boundary = max(
                (idx for idx in _break_positions(text, search_start, limit)),
                default=-1,
            )
            if boundary > position:
                end = boundary + 1
        raw_chunk = text[position:end]
        chunk_text = raw_chunk.strip()
        if chunk_text:
            leading = len(raw_chunk) - len(raw_chunk.lstrip())
            trailing = len(raw_chunk) - len(raw_chunk.rstrip())
            chunks.append((position + leading, end - trailing, chunk_text))
        position = end
    return chunks


def _break_positions(text: str, start: int, end: int) -> list[int]:
    return [index for index in range(start, end) if text[index] in "\n。！？.!?；;,， "]


def _excerpt(text: str, limit: int) -> str:
    excerpt = text.strip()
    return excerpt if len(excerpt) <= limit else excerpt[:limit].rstrip() + "..."


def _chunk_id(
    source_type: MaterialSourceType,
    source_id: str,
    locator: Mapping[str, Any],
    text: str,
) -> str:
    anchor = "\x1f".join(
        [
            source_type,
            source_id,
            str(
                locator.get("path")
                or locator.get("record_id")
                or locator.get("attachment_id")
                or ""
            ),
            str(locator.get("chunk_index") or ""),
            str(locator.get("char_start") or ""),
            text,
        ]
    )
    return f"material-{sha256(anchor.encode('utf-8')).hexdigest()[:24]}"
