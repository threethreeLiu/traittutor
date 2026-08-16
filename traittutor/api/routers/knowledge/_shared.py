"""
Knowledge Base API Router
=========================

Handles knowledge base CRUD operations, file uploads, and initialization.
"""

from datetime import datetime
import functools
import logging
import os
from pathlib import Path
import re
from uuid import uuid4

from fastapi import (
    HTTPException,
    UploadFile,
)
from pydantic import BaseModel, ConfigDict

from traittutor.api.utils.task_id_manager import TaskIDManager
from traittutor.api.utils.task_log_stream import get_task_stream_manager
from traittutor.knowledge.kb_types import is_connected_kb
from traittutor.knowledge.manager import KnowledgeBaseManager
from traittutor.multi_user.knowledge_access import (
    assert_writable,
    current_kb_base_dir,
    current_kb_manager,
    manager_for_resource,
    resolve_kb,
)
from traittutor.services.config import PROJECT_ROOT, load_config_with_main
from traittutor.services.rag.factory import (
    DEFAULT_PROVIDER,
    GRAPHRAG_PROVIDER,
    KNOWN_PROVIDERS,
    LIGHTRAG_PROVIDER,
    PAGEINDEX_PROVIDER,
)
from traittutor.utils.document_validator import DocumentValidator
from traittutor.utils.error_utils import format_exception_message

# Initialize logger with config
config = load_config_with_main("main.yaml", PROJECT_ROOT)
log_dir = config.get("paths", {}).get("user_log_dir") or config.get("logging", {}).get("log_dir")
_shared_logger = logging.getLogger(__name__)


# Initialize logger with config
config = load_config_with_main("main.yaml", PROJECT_ROOT)

log_dir = config.get("paths", {}).get("user_log_dir") or config.get("logging", {}).get("log_dir")

logger = logging.getLogger(__name__)

# Constants for byte conversions
BYTES_PER_GB = 1024**3

BYTES_PER_MB = 1024**2


def format_bytes_human_readable(size_bytes: int) -> str:
    """Format bytes into human-readable string (GB, MB, or bytes)."""
    if size_bytes >= BYTES_PER_GB:
        return f"{size_bytes / BYTES_PER_GB:.1f} GB"
    elif size_bytes >= BYTES_PER_MB:
        return f"{size_bytes / BYTES_PER_MB:.1f} MB"
    else:
        return f"{size_bytes} bytes"


_kb_base_dir = PROJECT_ROOT / "data" / "knowledge_bases"

DEFAULT_KB_ALIASES = {"", "default", "current", "selected", "默认", "默认知识库", "当前知识库"}


def get_kb_manager():
    """Return the current owner-scoped knowledge-base manager."""
    return current_kb_manager()


def _current_kb_base_dir() -> Path:
    return current_kb_base_dir()


def _writable_kb(kb_name: str) -> tuple[KnowledgeBaseManager, str, Path]:
    resource = assert_writable(kb_name)
    return manager_for_resource(resource), resource.name, resource.base_dir


class KnowledgeBaseInfo(BaseModel):
    id: str | None = None
    name: str
    is_default: bool
    statistics: dict
    metadata: dict | None = None
    path: str | None = None
    status: str | None = None
    progress: dict | None = None
    source: str | None = None
    assigned: bool = False
    read_only: bool = False
    provenance_label: str | None = None
    available: bool = True


class KBConfigUpdateRequest(BaseModel):
    """Typed whitelist for the generic KB config editor.

    Only provider selection is editable here. Filesystem pointer fields
    (``type``/``external_path``/``vault_path``/``storage_path``) are only set
    by the dedicated connect-folder / connect-obsidian flows, which enforce
    the path allowlist; pipeline bookkeeping (``status``/``progress``/
    ``needs_reindex``) stays server-owned. Unknown fields are rejected.
    """

    model_config = ConfigDict(extra="forbid")

    rag_provider: str | None = None


class LinkFolderRequest(BaseModel):
    """Request model for linking a local folder to a KB."""

    folder_path: str


class LinkedFolderInfo(BaseModel):
    """Response model for linked folder information."""

    id: str
    path: str
    added_at: str
    file_count: int


class SupportedFileTypesInfo(BaseModel):
    """Upload constraints exposed to the web client."""

    extensions: list[str]
    accept: str
    max_file_size_bytes: int


IMAGE_ACCEPT_MIME_TYPES = {
    ".bmp": "image/bmp",
    ".gif": "image/gif",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".webp": "image/webp",
}


def _build_unique_task_id(task_type: str, task_key_prefix: str) -> str:
    task_manager = TaskIDManager.get_instance()
    task_key = f"{task_key_prefix}_{datetime.now().isoformat()}_{uuid4().hex[:8]}"
    return task_manager.generate_task_id(task_type, task_key)


def _mark_kb_queued_for_processing(
    manager: KnowledgeBaseManager,
    kb_name: str,
    task_id: str,
    message: str,
    *,
    status: str = "processing",
) -> None:
    """Flip an existing KB to a live processing status before its background task is dispatched.

    ``run_upload_processing_task`` only writes status once it starts running;
    without this pre-dispatch update the KB keeps reporting ``ready`` between
    the accepted upload/sync response and the task's first progress write.
    Mirrors the pre-dispatch update ``create_knowledge_base`` already does.
    ``stage`` must be a member of the frontend's ``LIVE_PROGRESS_STAGES`` set
    (web/lib/knowledge-helpers.ts).
    """
    manager.update_kb_status(
        name=kb_name,
        status=status,
        progress={
            "stage": "starting",
            "message": message,
            "percent": 0,
            "task_id": task_id,
            "timestamp": datetime.now().isoformat(),
        },
    )


def _save_zip_archive(
    file: UploadFile,
    sanitized_filename: str,
    target_dir: Path,
    allowed_extensions: set[str] | None,
) -> list[Path]:
    """Safely expand an uploaded ``.zip`` into ``target_dir``.

    The archive itself is never persisted; each member is validated and
    extracted via :func:`safe_extract_zip` (Zip Slip / zip-bomb / extension
    guards). Returns the list of written file paths.
    """
    import tempfile
    import zipfile

    from traittutor.utils.archive_extractor import ArchiveTooLargeError, safe_extract_zip

    file.file.seek(0)
    max_size = DocumentValidator.MAX_FILE_SIZE
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            tmp_path = Path(tmp.name)
            written = 0
            for chunk in iter(lambda: file.file.read(8192), b""):
                written += len(chunk)
                if written > max_size:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"Archive '{sanitized_filename}' exceeds maximum size limit of "
                            f"{format_bytes_human_readable(max_size)}"
                        ),
                    )
                tmp.write(chunk)

        try:
            result = safe_extract_zip(
                tmp_path, target_dir, allowed_extensions=allowed_extensions or set()
            )
        except ArchiveTooLargeError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Rejected archive '{sanitized_filename}': {exc}",
            ) from exc
        except zipfile.BadZipFile as exc:
            raise HTTPException(
                status_code=400,
                detail=f"'{sanitized_filename}' is not a valid zip archive.",
            ) from exc

        if not result.extracted:
            raise HTTPException(
                status_code=400,
                detail=f"Archive '{sanitized_filename}' contained no supported files.",
            )
        return result.extracted
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


# Folder organization is purely a human-facing layout: folders are real
# subdirectories under ``raw/`` (no manifest, no retrieval effect). These
# helpers keep user-supplied relative paths safe before they touch the FS.
_BAD_PATH_CHARS = re.compile(r'[\\:*?"<>|\x00-\x1f]')


def _sanitize_path_segment(segment: str) -> str:
    """Sanitize a single folder/file path segment for safe FS use."""
    cleaned = _BAD_PATH_CHARS.sub("", segment).strip().strip(".")
    return cleaned[:128]


def _sanitize_rel_subdir(rel_path: str | None) -> str:
    """Return a safe POSIX relative subdir (folders only, no traversal).

    A leading/trailing or interior ``..``/absolute marker raises 400 so a
    crafted directory upload can never escape ``raw/``.
    """
    if not rel_path:
        return ""
    parts: list[str] = []
    for raw_seg in str(rel_path).replace("\\", "/").split("/"):
        seg = raw_seg.strip()
        if seg in ("", "."):
            continue
        if seg == "..":
            raise HTTPException(status_code=400, detail="Invalid folder path")
        safe = _sanitize_path_segment(seg)
        if safe:
            parts.append(safe)
    return "/".join(parts)


def _safe_join_raw(raw_dir: Path, rel_path: str) -> Path:
    """Resolve ``rel_path`` under ``raw_dir``, rejecting traversal."""
    target = (raw_dir / rel_path).resolve()
    try:
        target.relative_to(raw_dir.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Access denied") from exc
    return target


def _save_uploaded_files(
    files: list[UploadFile],
    target_dir: Path,
    allowed_extensions: set[str] | None = None,
    rel_paths: list[str] | None = None,
) -> tuple[list[str], list[str]]:
    """Save uploaded files to the canonical local raw-material directory."""
    uploaded_files: list[str] = []
    uploaded_file_paths: list[str] = []
    written_file_paths: list[Path] = []

    try:
        for idx, file in enumerate(files):
            file_path = None
            original_filename = file.filename or "upload"
            try:
                sanitized_filename = DocumentValidator.validate_upload_safety(
                    original_filename,
                    _get_upload_file_size(file),
                    allowed_extensions=allowed_extensions,
                )
                file.filename = sanitized_filename

                # Directory uploads carry a relative path (folder/sub/file); the
                # folder portion is preserved under raw/ so nested structure is
                # kept verbatim. Single-file uploads have no rel path → root.
                rel = (
                    rel_paths[idx].replace("\\", "/")
                    if rel_paths and idx < len(rel_paths) and rel_paths[idx]
                    else ""
                )
                subdir = _sanitize_rel_subdir(rel.rsplit("/", 1)[0]) if "/" in rel else ""
                dest_dir = target_dir / subdir if subdir else target_dir
                if subdir:
                    dest_dir.mkdir(parents=True, exist_ok=True)
                rel_name = f"{subdir}/{sanitized_filename}" if subdir else sanitized_filename

                if Path(sanitized_filename).suffix.lower() == ".zip":
                    # Expand the archive in place; register each extracted
                    # member instead of the zip itself.
                    for dest in _save_zip_archive(
                        file, sanitized_filename, dest_dir, allowed_extensions
                    ):
                        written_file_paths.append(dest)
                        uploaded_files.append(dest.relative_to(target_dir).as_posix())
                        uploaded_file_paths.append(str(dest))
                    continue

                file_path = dest_dir / sanitized_filename
                max_size = DocumentValidator.MAX_FILE_SIZE
                written_bytes = 0

                file.file.seek(0)
                reader = functools.partial(file.file.read, 8192)
                with open(file_path, "wb") as buffer:
                    for chunk in iter(reader, b""):
                        written_bytes += len(chunk)
                        if written_bytes > max_size:
                            size_str = format_bytes_human_readable(max_size)
                            raise HTTPException(
                                status_code=400,
                                detail=(
                                    f"File '{sanitized_filename}' exceeds maximum size "
                                    f"limit of {size_str}"
                                ),
                            )
                        buffer.write(chunk)

                DocumentValidator.validate_upload_safety(
                    sanitized_filename, written_bytes, allowed_extensions=allowed_extensions
                )
                written_file_paths.append(file_path)
                uploaded_files.append(rel_name)
                uploaded_file_paths.append(str(file_path))

            except Exception as e:
                if file_path and file_path.exists():
                    try:
                        os.unlink(file_path)
                    except OSError:
                        pass

                error_message = f"Validation failed for file '{original_filename}': {format_exception_message(e)}"
                logger.error(error_message, exc_info=True)
                raise HTTPException(status_code=400, detail=error_message) from e
    except Exception:
        for written_path in written_file_paths:
            if written_path.exists():
                try:
                    os.unlink(written_path)
                except OSError:
                    pass
        raise

    return uploaded_files, uploaded_file_paths


def _get_upload_file_size(file: UploadFile) -> int | None:
    """Best-effort byte size detection without consuming the uploaded stream."""
    try:
        current_position = file.file.tell()
        file.file.seek(0, os.SEEK_END)
        size = file.file.tell()
        file.file.seek(current_position)
        return size
    except Exception:
        return None


def _validate_upload_batch(
    files: list[UploadFile],
    allowed_extensions: set[str] | None = None,
    rel_paths: list[str] | None = None,
) -> list[dict[str, int | str | None]]:
    """Validate upload metadata before mutating KB state or writing any files."""
    validated: list[dict[str, int | str | None]] = []
    seen_names: set[str] = set()

    for idx, file in enumerate(files):
        original_filename = file.filename or "upload"
        size_bytes = _get_upload_file_size(file)
        try:
            sanitized_filename = DocumentValidator.validate_upload_safety(
                original_filename,
                size_bytes,
                allowed_extensions=allowed_extensions,
            )
        except Exception as e:
            error_message = (
                f"Validation failed for file '{original_filename}': {format_exception_message(e)}"
            )
            raise HTTPException(status_code=400, detail=error_message) from e

        rel = (
            rel_paths[idx].replace("\\", "/")
            if rel_paths and idx < len(rel_paths) and rel_paths[idx]
            else ""
        )
        subdir = _sanitize_rel_subdir(rel.rsplit("/", 1)[0]) if "/" in rel else ""
        duplicate_key = f"{subdir}/{sanitized_filename}" if subdir else sanitized_filename

        if duplicate_key in seen_names:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Duplicate filename after sanitization: '{duplicate_key}'. "
                    "Rename one of the files and try again."
                ),
            )

        seen_names.add(duplicate_key)
        validated.append(
            {
                "original_filename": original_filename,
                "sanitized_filename": sanitized_filename,
                "path": duplicate_key,
                "size_bytes": size_bytes,
            }
        )

    return validated


def _task_log(task_id: str, message: str, level: str = "info") -> None:
    manager = get_task_stream_manager()
    manager.ensure_task(task_id)
    manager.emit_log(task_id, message)

    log_method = getattr(logger, level, None)
    if callable(log_method):
        log_method(f"[{task_id}] {message}")
    else:
        logger.info(f"[{task_id}] {message}")


def _validate_registered_provider(raw_provider: str | None) -> str:
    """Validate an explicit canonical provider id."""
    provider = str(raw_provider or "").strip().lower()
    if provider not in KNOWN_PROVIDERS:
        raise HTTPException(status_code=422, detail="A registered RAG provider is required")
    return provider


def _assert_provider_ready(provider: str) -> None:
    """Block creating/using a KB whose engine isn't ready.

    PageIndex needs an API key; GraphRAG needs the optional package installed.
    """
    if provider == PAGEINDEX_PROVIDER:
        from traittutor.services.rag.pipelines.pageindex.config import is_pageindex_configured

        if not is_pageindex_configured():
            raise HTTPException(
                status_code=400,
                detail=(
                    "PageIndex API key is not configured. Add it under "
                    "Knowledge → RAG pipeline settings before creating a PageIndex "
                    "knowledge base."
                ),
            )

    if provider == GRAPHRAG_PROVIDER:
        from traittutor.services.rag.pipelines.graphrag.config import is_graphrag_available

        if not is_graphrag_available():
            raise HTTPException(
                status_code=400,
                detail=(
                    "GraphRAG is not installed. Run "
                    "`pip install 'traittutor[graphrag]'` on the server before "
                    "creating a GraphRAG knowledge base."
                ),
            )

    if provider == LIGHTRAG_PROVIDER:
        from traittutor.services.rag.pipelines.lightrag.config import is_lightrag_available

        if not is_lightrag_available():
            raise HTTPException(
                status_code=400,
                detail=(
                    "LightRAG is not installed. Run "
                    "`pip install 'traittutor[rag-lightrag]'` on the server before "
                    "creating a LightRAG knowledge base."
                ),
            )


def _enforce_provider_formats(provider: str, files: list[UploadFile]) -> None:
    """Reject files PageIndex's document endpoint does not accept, up front."""
    if provider != PAGEINDEX_PROVIDER:
        return
    from traittutor.services.rag.pipelines.pageindex.pipeline import SUPPORTED_EXTENSIONS

    unsupported = [
        f.filename
        for f in files
        if f.filename
        and not f.filename.lower().endswith(".zip")
        and Path(f.filename).suffix.lower() not in SUPPORTED_EXTENSIONS
    ]
    if unsupported:
        supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise HTTPException(
            status_code=400,
            detail=(
                f"PageIndex knowledge bases accept: {supported}. "
                f"Unsupported: {', '.join(unsupported[:5])}."
            ),
        )


def _resolve_registered_kb_name(manager: KnowledgeBaseManager, kb_name: str | None) -> str:
    """Resolve route-level default aliases to the configured default KB."""
    requested = str(kb_name or "").strip()
    kb_names = manager.list_knowledge_bases()
    if requested and requested in kb_names:
        return requested

    if requested.lower() in DEFAULT_KB_ALIASES:
        default_kb = manager.get_default()
        if default_kb and default_kb in kb_names:
            return default_kb
        raise HTTPException(status_code=404, detail="No default knowledge base is configured")

    raise HTTPException(status_code=404, detail=f"Knowledge base '{requested}' not found")


def _load_kb_entry_or_404(manager: KnowledgeBaseManager, kb_name: str) -> dict:
    manager.config = manager._load_config()
    kb_entry = manager.config.get("knowledge_bases", {}).get(kb_name)
    if kb_entry is None:
        raise HTTPException(status_code=404, detail=f"Knowledge base '{kb_name}' not found")
    return kb_entry


def _assert_not_connected_kb(kb_name: str, kb_entry: dict) -> None:
    """Block writes to connected KBs (Obsidian vaults, linked indexes).

    They are read-only pointers to the user's external files — we never write
    into or re-index them.
    """
    if is_connected_kb(kb_entry):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Knowledge base '{kb_name}' is connected to an external folder and is "
                "read-only. Uploads and re-indexing are not available for it."
            ),
        )


def _assert_kb_writable_or_409(kb_name: str, kb_entry: dict) -> None:
    _assert_not_connected_kb(kb_name, kb_entry)
    if bool(kb_entry.get("needs_reindex", False)):
        raise HTTPException(
            status_code=409,
            detail=(f"Knowledge base '{kb_name}' requires reindex before accepting uploads."),
        )


def _matching_index_is_valid(kb_name: str, matching_version: dict | None) -> bool:
    """Return whether a matching active index can safely satisfy retrieval."""
    if not matching_version:
        return False
    try:
        from traittutor.services.rag.index_probe import inspect_provider_version
        from traittutor.services.rag.pipelines.llamaindex.storage import (
            validate_storage_embeddings,
        )

        probe = inspect_provider_version(matching_version, DEFAULT_PROVIDER)
        if not probe.ready:
            logger.warning(
                "Matching index for KB '%s' is not provider-ready; forcing re-index: %s",
                kb_name,
                probe.failure_summary or probe.diagnostics,
            )
            return False
        validate_storage_embeddings(Path(str(matching_version["storage_path"])))
        return True
    except Exception as exc:
        logger.warning(
            "Matching index for KB '%s' is invalid; forcing re-index: %s",
            kb_name,
            exc,
        )
        return False


class ProviderModeUpdate(BaseModel):
    """Set an engine's global default retrieval mode (from its engine card)."""

    mode: str


class PageIndexConfigUpdate(BaseModel):
    # Tri-state api_key: omit/None keeps the stored key, "" clears it, any other
    # value replaces it — so the masked UI never round-trips the real secret.
    api_key: str | None = None
    api_base_url: str | None = None


class LlamaIndexConfigUpdate(BaseModel):
    """Partial update for the LlamaIndex engine knobs (omitted fields kept)."""

    retrieval_profile: str | None = None
    top_k: int | None = None
    vector_top_k_multiplier: int | None = None
    bm25_top_k_multiplier: int | None = None
    chunk_size: int | None = None
    chunk_overlap: int | None = None


class GraphRagConfigUpdate(BaseModel):
    """Partial update for GraphRAG query knobs (omitted fields kept)."""

    response_type: str | None = None
    community_level: int | None = None
    dynamic_community_selection: bool | None = None


class LightRagConfigUpdate(BaseModel):
    """Partial update for LightRAG query knobs (omitted fields kept)."""

    top_k: int | None = None
    response_type: str | None = None


# Model kinds an engine page is allowed to read/switch. ``vision`` is not a
# catalog service (it rides on the active chat model), so it is intentionally
# excluded here.
_ENGINE_MODEL_KINDS = ("llm", "embedding")


class ActiveModelUpdate(BaseModel):
    """Switch the globally-active model for a kind (llm / embedding)."""

    kind: str
    profile_id: str
    model_id: str


class ConnectObsidianRequest(BaseModel):
    name: str
    vault_path: str


class ProbeFolderRequest(BaseModel):
    folder_path: str
    rag_provider: str = DEFAULT_PROVIDER


class ConnectFolderRequest(BaseModel):
    name: str
    folder_path: str
    rag_provider: str = DEFAULT_PROVIDER


class ProbeLightRagServerRequest(BaseModel):
    server_url: str
    api_key: str = ""


class ConnectLightRagServerRequest(BaseModel):
    name: str
    server_url: str
    api_key: str = ""
    search_mode: str = ""


def _resolve_kb_raw_dir(kb_name: str) -> Path:
    """Resolve the raw/ directory for a KB, validating that it exists."""
    resource = resolve_kb(kb_name)
    manager = manager_for_resource(resource)
    kb_path = manager.get_knowledge_base_path(resource.name)
    return kb_path / "raw"


def _resolve_kb_raw_file_or_404(kb_name: str, filename: str) -> Path:
    """Resolve a raw KB file while preventing traversal outside raw/."""
    raw_dir = _resolve_kb_raw_dir(kb_name)
    if not raw_dir.exists():
        raise HTTPException(status_code=404, detail="File not found")

    raw_resolved = raw_dir.resolve()
    target = (raw_dir / filename).resolve()
    try:
        target.relative_to(raw_resolved)
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied") from None

    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    return target


class CreateFolderPayload(BaseModel):
    path: str


class MoveFilePayload(BaseModel):
    source: str
    dest_folder: str = ""


__all__ = [
    "config",
    "log_dir",
    "logger",
    "BYTES_PER_GB",
    "BYTES_PER_MB",
    "format_bytes_human_readable",
    "_kb_base_dir",
    "DEFAULT_KB_ALIASES",
    "get_kb_manager",
    "_current_kb_base_dir",
    "_writable_kb",
    "KnowledgeBaseInfo",
    "KBConfigUpdateRequest",
    "LinkFolderRequest",
    "LinkedFolderInfo",
    "SupportedFileTypesInfo",
    "IMAGE_ACCEPT_MIME_TYPES",
    "_build_unique_task_id",
    "_mark_kb_queued_for_processing",
    "_save_zip_archive",
    "_BAD_PATH_CHARS",
    "_sanitize_path_segment",
    "_sanitize_rel_subdir",
    "_safe_join_raw",
    "_save_uploaded_files",
    "_get_upload_file_size",
    "_validate_upload_batch",
    "_task_log",
    "_validate_registered_provider",
    "_assert_provider_ready",
    "_enforce_provider_formats",
    "_resolve_registered_kb_name",
    "_load_kb_entry_or_404",
    "_assert_not_connected_kb",
    "_assert_kb_writable_or_409",
    "_matching_index_is_valid",
    "ProviderModeUpdate",
    "PageIndexConfigUpdate",
    "LlamaIndexConfigUpdate",
    "GraphRagConfigUpdate",
    "LightRagConfigUpdate",
    "_ENGINE_MODEL_KINDS",
    "ActiveModelUpdate",
    "ConnectObsidianRequest",
    "ProbeFolderRequest",
    "ConnectFolderRequest",
    "ProbeLightRagServerRequest",
    "ConnectLightRagServerRequest",
    "_resolve_kb_raw_dir",
    "_resolve_kb_raw_file_or_404",
    "CreateFolderPayload",
    "MoveFilePayload",
]
