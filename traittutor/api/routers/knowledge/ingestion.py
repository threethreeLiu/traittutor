"""
Knowledge Base API Router
=========================

Handles knowledge base CRUD operations, file uploads, and initialization.
"""

from datetime import datetime
import functools
from pathlib import Path
import traceback

import anyio
from fastapi import (
    APIRouter,
    BackgroundTasks,
    File,
    Form,
    HTTPException,
    UploadFile,
)

# Initialize logger with config
from traittutor.api.routers.knowledge._shared import (  # noqa: F401
    _assert_kb_writable_or_409,
    _assert_not_connected_kb,
    _assert_provider_ready,
    _build_unique_task_id,
    _current_kb_base_dir,
    _enforce_provider_formats,
    _load_kb_entry_or_404,
    _mark_kb_queued_for_processing,
    _matching_index_is_valid,
    _save_uploaded_files,
    _task_log,
    _validate_registered_provider,
    _validate_upload_batch,
    _writable_kb,
    config,
    get_kb_manager,
)
from traittutor.api.routers.knowledge._shared import _shared_logger as logger
from traittutor.api.utils.task_id_manager import TaskIDManager
from traittutor.api.utils.task_log_stream import capture_task_logs, get_task_stream_manager
from traittutor.knowledge.add_documents import DocumentAdder
from traittutor.knowledge.initializer import KnowledgeBaseInitializer
from traittutor.knowledge.naming import validate_knowledge_base_name
from traittutor.knowledge.progress_tracker import ProgressStage, ProgressTracker
from traittutor.knowledge.state_store import KnowledgeStateStore
from traittutor.services.rag.factory import (
    DEFAULT_PROVIDER,
    provider_uses_embedding_versions,
)
from traittutor.services.rag.file_routing import FileTypeRouter
from traittutor.utils.error_utils import format_exception_message

router = APIRouter()


async def run_initialization_task(initializer: KnowledgeBaseInitializer, task_id: str):
    """Background task for knowledge base initialization"""
    task_manager = TaskIDManager.get_instance()
    task_stream_manager = get_task_stream_manager()
    task_stream_manager.ensure_task(task_id)

    with capture_task_logs(task_id):
        try:
            if not initializer.progress_tracker:
                initializer.progress_tracker = ProgressTracker(
                    initializer.kb_name, initializer.base_dir
                )

            initializer.progress_tracker.task_id = task_id

            _task_log(task_id, f"Initializing knowledge base '{initializer.kb_name}'")

            await initializer.process_documents()
            _task_log(task_id, "Document processing complete")
            _task_log(task_id, "Finalizing initialization")
            indexed_count = len(
                FileTypeRouter.collect_supported_files(initializer.raw_dir, recursive=True)
            )

            initializer.progress_tracker.update(
                ProgressStage.COMPLETED,
                "Knowledge base initialization complete!",
                current=1,
                total=1,
                indexed_count=indexed_count,
                index_changed=True,
                index_action="create",
            )

            manager = get_kb_manager()
            manager.update_kb_status(
                name=initializer.kb_name,
                status="ready",
                progress={
                    "stage": "completed",
                    "message": "Knowledge base initialization complete!",
                    "percent": 100,
                    "current": 1,
                    "total": 1,
                    "task_id": task_id,
                    "timestamp": datetime.now().isoformat(),
                    "indexed_count": indexed_count,
                    "index_changed": True,
                    "index_action": "create",
                },
            )

            _task_log(
                task_id, f"Knowledge base '{initializer.kb_name}' initialized", level="success"
            )
            task_manager.update_task_status(task_id, "completed")
            task_stream_manager.emit_complete(
                task_id, f"Knowledge base '{initializer.kb_name}' initialization complete"
            )
        except Exception as e:
            import traceback as _tb

            trace = _tb.format_exc()
            # Exception text and stack trace stay server-side: they can carry
            # absolute paths and provider internals. The client gets a stable
            # message on every task surface (log stream, status, SSE).
            error_msg = "Initialization failed"
            logger.error(f"KB initialization task '{task_id}' failed: {e}\n{trace}")

            _task_log(task_id, f"Initialization failed: {error_msg}", level="error")

            task_manager.update_task_status(task_id, "error", error=error_msg)

            manager = get_kb_manager()
            manager.update_kb_status(
                name=initializer.kb_name,
                status="error",
                progress={
                    "stage": "error",
                    "message": f"Initialization failed: {error_msg}",
                    "percent": 0,
                    "error": error_msg,
                    "task_id": task_id,
                    "timestamp": datetime.now().isoformat(),
                },
            )

            if initializer.progress_tracker:
                initializer.progress_tracker.update(
                    ProgressStage.ERROR, f"Initialization failed: {error_msg}", error=error_msg
                )
            task_stream_manager.emit_failed(task_id, error_msg)


async def run_upload_processing_task(
    kb_name: str,
    base_dir: str,
    uploaded_file_paths: list[str],
    task_id: str,
    rag_provider: str = None,
    folder_id: str = None,
):
    """Background task for processing uploaded files.

    Args:
        kb_name: Knowledge base name
        base_dir: Base directory for knowledge bases
        uploaded_file_paths: List of file paths to process
        rag_provider: RAG provider already matched against the KB binding
        folder_id: Optional folder ID for sync state update
    """
    task_manager = TaskIDManager.get_instance()
    task_stream_manager = get_task_stream_manager()
    task_stream_manager.ensure_task(task_id)

    progress_tracker = ProgressTracker(kb_name, Path(base_dir))
    progress_tracker.task_id = task_id

    with capture_task_logs(task_id):
        try:
            _task_log(task_id, f"Processing {len(uploaded_file_paths)} file(s) for KB '{kb_name}'")
            progress_tracker.update(
                ProgressStage.PROCESSING_DOCUMENTS,
                f"Processing {len(uploaded_file_paths)} files...",
                current=0,
                total=len(uploaded_file_paths),
            )

            adder = DocumentAdder(
                kb_name=kb_name,
                base_dir=base_dir,
                progress_tracker=progress_tracker,
                rag_provider=rag_provider,
            )

            staged_files = adder.add_documents(uploaded_file_paths, allow_duplicates=False)
            _task_log(task_id, f"Staged {len(staged_files)} new file(s)")

            if not staged_files:
                _task_log(task_id, "No new files to process (all duplicates or invalid)")
                progress_tracker.update(
                    ProgressStage.COMPLETED,
                    "No new files to process (all duplicates or invalid)",
                    current=0,
                    total=0,
                )
                task_manager.update_task_status(task_id, "completed")
                task_stream_manager.emit_complete(
                    task_id, "No new files to process (all duplicates or invalid)"
                )
                return

            index_result = await adder.process_new_documents(staged_files)
            processed_files = index_result.processed_files
            _task_log(task_id, f"Indexed {index_result.processed_count} file(s)")

            if index_result.has_failures:
                failure_summary = index_result.failure_summary()
                error_msg = (
                    f"Indexed {index_result.processed_count}/{len(staged_files)} file(s); "
                    f"{index_result.failed_count} failed: {failure_summary}"
                )
                _task_log(task_id, error_msg, level="error")
                for failure in index_result.failures:
                    _task_log(
                        task_id,
                        f"Failed to index {failure.file_path.name}: {failure.error}",
                        level="error",
                    )
                progress_tracker.update(
                    ProgressStage.ERROR,
                    f"Processing failed: {error_msg}",
                    current=index_result.processed_count,
                    total=len(staged_files),
                    error=error_msg,
                    indexed_count=index_result.processed_count,
                    index_changed=index_result.processed_count > 0,
                    index_action="upload",
                )
                task_manager.update_task_status(task_id, "error", error=error_msg)
                task_stream_manager.emit_failed(
                    task_id,
                    error_msg,
                    details="\n".join(
                        f"{failure.file_path}: {failure.error}" for failure in index_result.failures
                    ),
                )
                return

            adder.update_metadata(index_result.processed_count)

            if folder_id and processed_files:
                try:
                    manager = get_kb_manager()
                    manager.update_folder_sync_state(
                        kb_name, folder_id, [str(f) for f in processed_files]
                    )
                    _task_log(task_id, f"Updated folder sync state: {folder_id}")
                except Exception as sync_err:
                    _task_log(
                        task_id, f"Folder sync state update failed: {sync_err}", level="warning"
                    )

            num_processed = index_result.processed_count
            progress_tracker.update(
                ProgressStage.COMPLETED,
                f"Successfully processed {num_processed} files!",
                current=num_processed,
                total=num_processed,
                indexed_count=num_processed,
                index_changed=num_processed > 0,
                index_action="upload",
            )

            _task_log(
                task_id, f"Processed {num_processed} file(s) for '{kb_name}'", level="success"
            )
            task_manager.update_task_status(task_id, "completed")
            task_stream_manager.emit_complete(
                task_id, f"Successfully processed {num_processed} files for '{kb_name}'"
            )
        except Exception as e:
            import traceback as _tb

            trace = _tb.format_exc()
            # See the initialization task: internals stay in the server log.
            error_msg = f"Upload processing failed (KB '{kb_name}')"
            logger.error(f"KB upload task '{task_id}' failed: {e}\n{trace}")
            _task_log(task_id, error_msg, level="error")

            task_manager.update_task_status(task_id, "error", error=error_msg)

            progress_tracker.update(
                ProgressStage.ERROR, f"Processing failed: {error_msg}", error=error_msg
            )
            task_stream_manager.emit_failed(task_id, error_msg)


@router.post("/{kb_name}/upload")
async def upload_files(
    kb_name: str,
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    rag_provider: str = Form(None),
    rel_paths: list[str] = Form(None),
):
    """Upload files to a knowledge base and process them in background."""
    try:
        manager, kb_name, kb_base_dir = _writable_kb(kb_name)
        kb_path = manager.get_knowledge_base_path(kb_name)
        raw_dir = kb_path / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)

        requested_provider = None
        if rag_provider is not None and str(rag_provider).strip():
            requested_provider = _validate_registered_provider(rag_provider)

        kb_entry = _load_kb_entry_or_404(manager, kb_name)
        _assert_kb_writable_or_409(kb_name, kb_entry)
        kb_provider = _validate_registered_provider(
            kb_entry.get("rag_provider") or DEFAULT_PROVIDER
        )
        if requested_provider and requested_provider != kb_provider:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Requested provider '{requested_provider}' does not match KB provider '{kb_provider}'. "
                    "A knowledge base is locked to the engine it was created with."
                ),
            )
        _assert_provider_ready(kb_provider)
        _enforce_provider_formats(kb_provider, files)
        allowed_extensions = FileTypeRouter.get_supported_extensions()
        # ``.zip`` is accepted as an upload container; its members are
        # validated against ``allowed_extensions`` during extraction and the
        # archive itself is never indexed (``safe_extract_zip`` skips ``.zip``).
        upload_extensions = allowed_extensions | {".zip"}
        _validate_upload_batch(files, allowed_extensions=upload_extensions, rel_paths=rel_paths)
        # 8KB-chunk disk writes (up to 200MB per file) must not pin the
        # event loop; the spooled uploads are quiesced while we await.
        uploaded_files, uploaded_file_paths = await anyio.to_thread.run_sync(
            functools.partial(
                _save_uploaded_files,
                files,
                raw_dir,
                allowed_extensions=upload_extensions,
                rel_paths=rel_paths,
            )
        )
        task_id = _build_unique_task_id("kb_upload", kb_name)
        get_task_stream_manager().ensure_task(task_id)

        logger.info(f"Uploading {len(uploaded_files)} files to KB '{kb_name}'")

        _mark_kb_queued_for_processing(
            manager,
            kb_name,
            task_id,
            f"Processing {len(uploaded_files)} uploaded file(s)...",
        )

        background_tasks.add_task(
            run_upload_processing_task,
            kb_name=kb_name,
            base_dir=str(kb_base_dir),
            uploaded_file_paths=uploaded_file_paths,
            task_id=task_id,
            rag_provider=kb_provider,
        )

        return {
            "message": f"Uploaded {len(uploaded_files)} files. Processing in background.",
            "files": uploaded_files,
            "task_id": task_id,
        }
    except HTTPException:
        raise
    except ValueError:
        raise HTTPException(
            status_code=404, detail=f"Knowledge base '{kb_name}' not found"
        ) from None
    except Exception as e:
        # Unexpected failure (Server error)
        formatted_error = format_exception_message(e)
        raise HTTPException(status_code=500, detail=formatted_error) from e


@router.post("/create")
async def create_knowledge_base(
    background_tasks: BackgroundTasks,
    name: str = Form(...),
    files: list[UploadFile] = File(...),
    rag_provider: str = Form(DEFAULT_PROVIDER),
    rel_paths: list[str] = Form(None),
):
    """Create a new knowledge base and initialize it with files."""
    try:
        try:
            name = validate_knowledge_base_name(name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        manager = get_kb_manager()
        kb_base_dir = _current_kb_base_dir()
        if name in manager.list_knowledge_bases():
            raise HTTPException(status_code=400, detail=f"Knowledge base '{name}' already exists")

        rag_provider = _validate_registered_provider(rag_provider)
        _assert_provider_ready(rag_provider)
        _enforce_provider_formats(rag_provider, files)
        allowed_extensions = FileTypeRouter.get_supported_extensions()
        _validate_upload_batch(files, allowed_extensions=allowed_extensions, rel_paths=rel_paths)

        logger.info(f"Creating KB: {name} (provider={rag_provider})")
        task_id = _build_unique_task_id("kb_init", name)
        get_task_stream_manager().ensure_task(task_id)

        # Register the KB in canonical state with "initializing" status.
        # This ensures the KB appears in the list right away
        manager.update_kb_status(
            name=name,
            status="initializing",
            progress={
                "stage": "initializing",
                "message": "Initializing knowledge base...",
                "percent": 0,
                "current": 0,
                "total": len(files),
                "task_id": task_id,
            },
        )
        # Also store rag_provider in config (reload and update)
        manager.config = manager._load_config()
        if name in manager.config.get("knowledge_bases", {}):
            manager.config["knowledge_bases"][name]["rag_provider"] = rag_provider
            manager.config["knowledge_bases"][name]["needs_reindex"] = False
            manager._save_config()

        progress_tracker = ProgressTracker(name, kb_base_dir)

        initializer = KnowledgeBaseInitializer(
            kb_name=name,
            base_dir=str(kb_base_dir),
            progress_tracker=progress_tracker,
            rag_provider=rag_provider,
        )

        initializer.create_directory_structure()
        progress_tracker.task_id = task_id

        manager = get_kb_manager()
        if name not in manager.list_knowledge_bases():
            logger.warning(f"KB {name} not found in config, registering manually")
            initializer._register_to_config()

        uploaded_files, _ = await anyio.to_thread.run_sync(
            functools.partial(
                _save_uploaded_files,
                files,
                initializer.raw_dir,
                allowed_extensions=allowed_extensions,
                rel_paths=rel_paths,
            )
        )

        progress_tracker.update(
            ProgressStage.PROCESSING_DOCUMENTS,
            f"Saved {len(uploaded_files)} files, preparing to process...",
            current=0,
            total=len(uploaded_files),
        )

        background_tasks.add_task(run_initialization_task, initializer, task_id)

        logger.info(f"KB '{name}' created, processing {len(uploaded_files)} files in background")

        return {
            "message": f"Knowledge base '{name}' created. Processing {len(uploaded_files)} files in background.",
            "name": name,
            "files": uploaded_files,
            "task_id": task_id,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create KB: {e}")
        logger.debug(traceback.format_exc())
        raise HTTPException(status_code=500, detail="Internal server error") from e


async def run_reindex_task(kb_name: str, base_dir: str, task_id: str, signature_hash: str) -> None:
    """Re-index a KB's raw documents against the currently-active embedding config.

    Each ``(profile, model, dimension, base_url)`` combination gets its own
    flat ``<kb>/version-N/`` storage directory. Prior versions are preserved
    untouched so switching the active embedding model back to a
    previously-indexed one reuses the existing version with no extra work.
    """
    task_manager = TaskIDManager.get_instance()
    task_stream_manager = get_task_stream_manager()
    task_stream_manager.ensure_task(task_id)

    with capture_task_logs(task_id):
        try:
            base_path = Path(base_dir)
            kb_dir = base_path / kb_name
            raw_dir = kb_dir / "raw"
            if not raw_dir.is_dir():
                raise FileNotFoundError(f"KB '{kb_name}' has no `raw/` directory; cannot reindex.")
            file_paths = [
                str(path)
                for path in FileTypeRouter.collect_supported_files(raw_dir, recursive=True)
            ]
            if not file_paths:
                raise ValueError(f"KB '{kb_name}' has no source files in `raw/` to reindex.")

            _task_log(
                task_id,
                f"Re-indexing '{kb_name}' ({len(file_paths)} files) against signature {signature_hash}",
            )

            progress_tracker = ProgressTracker(kb_name, base_path)
            progress_tracker.task_id = task_id
            progress_tracker.update(
                ProgressStage.PROCESSING_DOCUMENTS,
                f"Re-indexing {len(file_paths)} document(s) with the active embedding model...",
                current=0,
                total=len(file_paths),
            )

            from traittutor.services.rag.service import RAGService

            # provider=None → RAGService resolves the KB's TraitTutor-bound
            # engine, so re-indexing a PageIndex/LightRAG/GraphRAG KB stays on
            # that provider rather than forcing the default pipeline.
            rag_service = RAGService(kb_base_dir=str(base_path), provider=None)

            def _on_progress(batch_num: int, total_batches: int) -> None:
                progress_tracker.update(
                    ProgressStage.PROCESSING_DOCUMENTS,
                    f"Embedding batches: {batch_num}/{total_batches}",
                    current=batch_num,
                    total=total_batches,
                )

            # The pipeline now raises the underlying error (embedding API
            # failure, parse error, etc.) so it surfaces in the task log
            # rather than being swallowed into a generic wrapper. A False
            # return is reserved for "no documents to index" — surface that
            # specifically too.
            success = await rag_service.initialize(
                kb_name=kb_name,
                file_paths=file_paths,
                progress_callback=_on_progress,
            )
            if not success:
                raise RuntimeError(f"Re-index found no valid documents to index in '{kb_name}'.")

            completed_at = datetime.now().isoformat()
            try:
                state = KnowledgeStateStore(base_path)
                metadata = state.load_metadata(kb_name)
                metadata["last_updated"] = completed_at
                metadata["last_indexed_at"] = completed_at
                metadata["last_indexed_count"] = len(file_paths)
                metadata["last_indexed_action"] = "reindex"
                state.save_metadata(kb_name, metadata)
            except Exception as meta_err:
                logger.warning(
                    "Failed to update re-index metadata for '%s': %s",
                    kb_name,
                    meta_err,
                )

            manager = get_kb_manager()
            manager.update_kb_status(
                name=kb_name,
                status="ready",
                progress={
                    "stage": "completed",
                    "message": "Re-index complete",
                    "percent": 100,
                    "current": len(file_paths),
                    "total": len(file_paths),
                    "task_id": task_id,
                    "timestamp": completed_at,
                    "indexed_count": len(file_paths),
                    "index_changed": True,
                    "index_action": "reindex",
                },
            )
            # Clear the legacy mismatch / needs_reindex flags now that an
            # index version matching the active config exists on disk.
            kb_entry = manager.config.get("knowledge_bases", {}).get(kb_name) or {}
            mutated = False
            if kb_entry.get("needs_reindex"):
                kb_entry["needs_reindex"] = False
                mutated = True
            if kb_entry.get("embedding_mismatch"):
                kb_entry.pop("embedding_mismatch", None)
                mutated = True
            if mutated:
                manager._save_config()

            _task_log(task_id, f"Re-index of '{kb_name}' complete", level="success")
            task_manager.update_task_status(task_id, "completed")
            task_stream_manager.emit_complete(task_id, f"Re-index of '{kb_name}' complete")
        except Exception as e:
            import traceback as _tb

            trace = _tb.format_exc()
            # See the initialization task: internals stay in the server log.
            error_msg = f"Re-index failed (KB '{kb_name}')"
            logger.error(f"KB re-index task '{task_id}' failed: {e}\n{trace}")
            _task_log(task_id, error_msg, level="error")
            task_manager.update_task_status(task_id, "error", error=error_msg)
            try:
                ProgressTracker(kb_name, Path(base_dir)).update(
                    ProgressStage.ERROR,
                    error_msg,
                    error=error_msg,
                )
            except Exception:
                pass
            task_stream_manager.emit_failed(task_id, error_msg)


@router.post("/{kb_name}/reindex")
async def reindex_knowledge_base(
    kb_name: str,
    background_tasks: BackgroundTasks,
):
    """Re-index ``kb_name`` through its bound RAG provider.

    LlamaIndex still keys versions by the active embedding model. The other
    providers keep synthetic provider-keyed versions, so they should rebuild
    without requiring an embedding-signature precheck.
    """
    try:
        manager, kb_name, kb_base_dir = _writable_kb(kb_name)
        kb_entry = _load_kb_entry_or_404(manager, kb_name)
        _assert_not_connected_kb(kb_name, kb_entry)
        force_reindex = str(kb_entry.get("status") or "").lower() == "error"
        kb_provider = _validate_registered_provider(
            kb_entry.get("rag_provider") or DEFAULT_PROVIDER
        )
        _assert_provider_ready(kb_provider)

        kb_dir = kb_base_dir / kb_name
        signature_hash = kb_provider
        if provider_uses_embedding_versions(kb_provider):
            from traittutor.services.rag.embedding_signature import signature_from_embedding_config
            from traittutor.services.rag.index_versioning import (
                find_matching_version,
            )

            signature = signature_from_embedding_config()
            if signature is None:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "No embedding model is configured. Set up the embedding "
                        "profile in Settings before re-indexing."
                    ),
                )

            signature_hash = signature.hash()
            matching_version = find_matching_version(kb_dir, signature)
            matching_valid = _matching_index_is_valid(kb_name, matching_version)
            if (
                matching_version
                and matching_version.get("layout") == "flat"
                and matching_valid
                and not force_reindex
            ):
                return {
                    "message": (
                        f"Knowledge base '{kb_name}' already has an index for the "
                        "active embedding configuration; no reindex needed."
                    ),
                    "task_id": None,
                    "signature": signature_hash,
                    "noop": True,
                }

        task_id = _build_unique_task_id("kb_reindex", kb_name)
        get_task_stream_manager().ensure_task(task_id)

        _mark_kb_queued_for_processing(
            manager, kb_name, task_id, "Queueing re-index...", status="initializing"
        )

        background_tasks.add_task(
            run_reindex_task,
            kb_name=kb_name,
            base_dir=str(kb_base_dir),
            task_id=task_id,
            signature_hash=signature_hash,
        )

        return {
            "message": f"Re-indexing '{kb_name}' in the background.",
            "task_id": task_id,
            "signature": signature_hash,
            "noop": False,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to start reindex for '{kb_name}': {e}")
        raise HTTPException(status_code=500, detail=format_exception_message(e)) from e


@router.post("/{kb_name}/retry")
async def retry_knowledge_base(
    kb_name: str,
    background_tasks: BackgroundTasks,
):
    """Retry a failed KB initialization/indexing run from its stored raw files."""
    try:
        manager, resolved_name, _ = _writable_kb(kb_name)
        kb_entry = _load_kb_entry_or_404(manager, resolved_name)
        status = str(kb_entry.get("status") or "").lower()
        progress_value = kb_entry.get("progress")
        progress = progress_value if isinstance(progress_value, dict) else {}
        progress_stage = str(progress.get("stage") or "").lower()
        if status != "error" and progress_stage != "error":
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Knowledge base '{resolved_name}' is not in an error state. "
                    "Use re-index when you want to rebuild a healthy knowledge base."
                ),
            )
        return await reindex_knowledge_base(resolved_name, background_tasks)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to retry KB '{kb_name}': {e}")
        raise HTTPException(status_code=500, detail=format_exception_message(e)) from e
