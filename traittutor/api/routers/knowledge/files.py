"""
Knowledge Base API Router
=========================

Handles knowledge base CRUD operations, file uploads, and initialization.
"""

import functools
import mimetypes
from pathlib import Path
import shutil
from typing import Any

import anyio
from fastapi import (
    APIRouter,
    HTTPException,
)
from fastapi.responses import FileResponse, PlainTextResponse

# Initialize logger with config
from traittutor.api.routers.knowledge._shared import (  # noqa: F401
    CreateFolderPayload,
    MoveFilePayload,
    _assert_kb_writable_or_409,
    _load_kb_entry_or_404,
    _resolve_kb_raw_dir,
    _resolve_kb_raw_file_or_404,
    _safe_join_raw,
    _sanitize_rel_subdir,
    _writable_kb,
)
from traittutor.knowledge.add_documents import remove_raw_document
from traittutor.utils.document_extractor import (
    MAX_EXTRACTED_CHARS_PER_DOC,
    DocumentExtractionError,
    extract_text_from_path,
)
from traittutor.utils.document_validator import DocumentValidator

router = APIRouter()


@router.get("/{kb_name}/files")
async def list_kb_raw_files(kb_name: str):
    """List raw documents under <kb>/raw/, recursing into folders.

    ``name`` is the POSIX path relative to ``raw/`` so the web client can
    rebuild the folder tree. Folders (including empty ones) are returned as
    ``type: "folder"`` entries so user-created/uploaded structure shows even
    before it holds any files. Folders are purely organizational and have no
    effect on indexing or retrieval.
    """
    raw_dir = _resolve_kb_raw_dir(kb_name)
    if not raw_dir.exists() or not raw_dir.is_dir():
        return {"files": []}

    files: list[dict[str, Any]] = []
    # Recursive directory scan with per-entry stat calls: keep it off the
    # event loop so a large raw/ tree cannot stall other requests.
    entries = await anyio.to_thread.run_sync(
        lambda: sorted(raw_dir.rglob("*"), key=lambda p: str(p).lower())
    )
    for entry in entries:
        rel = entry.relative_to(raw_dir).as_posix()
        if entry.is_dir():
            files.append({"name": rel, "type": "folder"})
            continue
        if not entry.is_file():
            continue
        try:
            stat = entry.stat()
        except OSError:
            continue
        media_type, _ = mimetypes.guess_type(entry.name)
        files.append(
            {
                "name": rel,
                "type": "file",
                "size": stat.st_size,
                "modified": stat.st_mtime,
                "mime_type": media_type,
            }
        )
    return {"files": files}


@router.post("/{kb_name}/folders")
async def create_kb_folder(kb_name: str, payload: CreateFolderPayload):
    """Create an (organizational) folder under <kb>/raw/. No retrieval effect."""
    manager, kb_name, _ = _writable_kb(kb_name)
    _assert_kb_writable_or_409(kb_name, _load_kb_entry_or_404(manager, kb_name))
    raw_dir = manager.get_knowledge_base_path(kb_name) / "raw"
    subdir = _sanitize_rel_subdir(payload.path)
    if not subdir:
        raise HTTPException(status_code=400, detail="Folder name is required")
    target = _safe_join_raw(raw_dir, subdir)
    target.mkdir(parents=True, exist_ok=True)
    return {"status": "ok", "path": subdir}


@router.post("/{kb_name}/files/move")
async def move_kb_file(kb_name: str, payload: MoveFilePayload):
    """Move a file/folder between organizational folders (display only).

    Moving never re-indexes: folders don't affect retrieval, so this is a pure
    filesystem relocation under ``raw/``.
    """
    manager, kb_name, _ = _writable_kb(kb_name)
    _assert_kb_writable_or_409(kb_name, _load_kb_entry_or_404(manager, kb_name))
    raw_dir = manager.get_knowledge_base_path(kb_name) / "raw"

    source_rel = _sanitize_rel_subdir(payload.source)
    if not source_rel:
        raise HTTPException(status_code=400, detail="Source path is required")
    src = _safe_join_raw(raw_dir, source_rel)
    if not src.exists():
        raise HTTPException(status_code=404, detail="Source not found")

    dest_folder = _sanitize_rel_subdir(payload.dest_folder)
    dest_dir = _safe_join_raw(raw_dir, dest_folder) if dest_folder else raw_dir.resolve()
    dest = dest_dir / src.name

    if dest.resolve() == src.resolve():
        return {"status": "ok", "path": source_rel}
    if src.is_dir() and dest_dir.resolve().is_relative_to(src.resolve()):
        raise HTTPException(status_code=400, detail="Cannot move a folder into itself")
    if dest.exists():
        raise HTTPException(
            status_code=409,
            detail=f"'{src.name}' already exists in the target folder",
        )

    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))
    return {"status": "ok", "path": dest.relative_to(raw_dir.resolve()).as_posix()}


@router.get("/{kb_name}/file-preview-text/{filename:path}")
async def serve_kb_raw_file_text_preview(kb_name: str, filename: str):
    """Serve extracted plain text for a raw KB document preview."""
    target = _resolve_kb_raw_file_or_404(kb_name, filename)
    try:
        # Document extraction is CPU/IO heavy (up to MAX_FILE_SIZE); run it
        # in a worker so the event loop keeps serving other requests.
        text = await anyio.to_thread.run_sync(
            functools.partial(
                extract_text_from_path,
                target,
                max_bytes=DocumentValidator.MAX_FILE_SIZE,
                max_chars=MAX_EXTRACTED_CHARS_PER_DOC,
            )
        )
    except DocumentExtractionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=404, detail="File not found") from exc

    return PlainTextResponse(text, media_type="text/plain; charset=utf-8")


@router.get("/{kb_name}/files/{filename:path}")
async def serve_kb_raw_file(kb_name: str, filename: str):
    """Serve a single raw document for inline preview / download.

    Resolution is sandboxed to the KB's raw/ directory; any path that
    escapes via traversal yields 403.
    """
    target = _resolve_kb_raw_file_or_404(kb_name, filename)
    media_type, _ = mimetypes.guess_type(target.name)
    return FileResponse(
        target,
        media_type=media_type or "application/octet-stream",
        filename=target.name,
        content_disposition_type="inline",
    )


@router.delete("/{kb_name}/files/{filename:path}")
async def delete_kb_file(kb_name: str, filename: str):
    """Remove a single raw document from a knowledge base.

    Unlike deleting the whole KB, this works while the KB is in an *error*
    state — that is the point: a file that failed to parse (e.g. one that
    exceeds the cloud parser's page limit) can be dropped without deleting and
    rebuilding everything. Connected (read-only) KBs are still rejected.
    Vectors are not pruned here; ``was_indexed`` tells the caller
    whether a re-index is needed to purge the file from retrieval.
    """
    manager, kb_name, _ = _writable_kb(kb_name)
    _assert_kb_writable_or_409(kb_name, _load_kb_entry_or_404(manager, kb_name))
    target = _resolve_kb_raw_file_or_404(kb_name, filename)

    kb_dir = manager.get_knowledge_base_path(kb_name)
    removal = remove_raw_document(Path(kb_dir), target)
    return {
        "status": "ok",
        "path": removal.rel_path,
        "was_indexed": removal.was_indexed,
    }
