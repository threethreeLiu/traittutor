"""
Knowledge Base API Router
=========================

Handles knowledge base CRUD operations, file uploads, and initialization.
"""

from fastapi import (
    APIRouter,
    BackgroundTasks,
    HTTPException,
)

# Initialize logger with config
from traittutor.api.routers.knowledge._shared import (  # noqa: F401
    ConnectFolderRequest,
    ConnectLightRagServerRequest,
    ConnectObsidianRequest,
    LinkedFolderInfo,
    LinkFolderRequest,
    ProbeFolderRequest,
    ProbeLightRagServerRequest,
    _assert_kb_writable_or_409,
    _assert_not_connected_kb,
    _build_unique_task_id,
    _load_kb_entry_or_404,
    _mark_kb_queued_for_processing,
    _validate_registered_provider,
    _writable_kb,
    get_kb_manager,
)
from traittutor.api.routers.knowledge._shared import _shared_logger as logger
from traittutor.api.routers.knowledge.ingestion import run_upload_processing_task
from traittutor.api.utils.task_log_stream import get_task_stream_manager
from traittutor.multi_user.knowledge_access import (
    manager_for_resource,
    resolve_kb,
)
from traittutor.services.rag.factory import (
    DEFAULT_PROVIDER,
)
from traittutor.services.rag.linked_kb import (
    assert_path_allowed,
    probe_linked_folder,
)

router = APIRouter()


@router.post("/connect-obsidian")
async def connect_obsidian_vault(payload: ConnectObsidianRequest):
    """Connect an existing Obsidian vault as a knowledge base.

    Registers a pointer to the user's vault directory (``type: obsidian``) — no
    upload, no index. The vault must be a directory the server can reach (i.e. a
    local/self-hosted deployment); the Obsidian capability reads it live.
    """
    name = (payload.name or "").strip()
    vault_path = (payload.vault_path or "").strip()
    if not name or not vault_path:
        raise HTTPException(status_code=400, detail="Both name and vault_path are required.")
    try:
        folder = assert_path_allowed(vault_path)
        manager = get_kb_manager()
        entry = manager.register_obsidian_vault(name, str(folder))
        return {"status": "connected", "name": name, "vault_path": entry["vault_path"]}
    except ValueError as e:
        # Missing/invalid path, disallowed location, or a name clash → 400.
        raise HTTPException(status_code=400, detail=str(e)) from e
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error connecting Obsidian vault: {e}")
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.post("/probe-folder")
async def probe_linked_folder_route(payload: ProbeFolderRequest):
    """Inspect a local folder for a ready engine index before linking it.

    Returns the probe verdict (ready index? embedding compatible? warnings?) so
    the UI can present and confirm before any registration happens. Does not
    create a knowledge base.
    """
    folder_path = (payload.folder_path or "").strip()
    if not folder_path:
        raise HTTPException(status_code=400, detail="folder_path is required.")
    try:
        folder = assert_path_allowed(folder_path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    result = probe_linked_folder(str(folder), payload.rag_provider)
    return result.to_dict()


@router.post("/connect-folder")
async def connect_linked_folder_route(payload: ConnectFolderRequest):
    """Mount an existing engine index as a read-only ``linked`` knowledge base.

    Re-probes server-side (never trusts the client's verdict), then registers a
    pointer to the folder. Retrieval reads the index in place — no copy, no
    re-index. Embedding-mismatch warnings do not block the link (the user may
    switch embedding models later); a missing/invalid index does.
    """
    name = (payload.name or "").strip()
    folder_path = (payload.folder_path or "").strip()
    if not name or not folder_path:
        raise HTTPException(status_code=400, detail="Both name and folder_path are required.")
    try:
        folder = assert_path_allowed(folder_path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    result = probe_linked_folder(str(folder), payload.rag_provider)
    if not result.ok:
        raise HTTPException(status_code=400, detail=result.error or "Folder is not linkable.")

    stats = {
        "embedding_model": result.embedding.index_model,
        "doc_count": result.doc_count,
    }
    try:
        manager = get_kb_manager()
        entry = manager.register_linked_kb(
            name,
            str(folder),
            result.provider,
            stats=stats,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error connecting linked folder: {e}")
        raise HTTPException(status_code=500, detail="Internal server error") from e

    return {
        "status": "connected",
        "name": name,
        "external_path": entry["external_path"],
        "rag_provider": entry["rag_provider"],
        "warnings": result.warnings,
    }


@router.post("/probe-lightrag-server")
async def probe_lightrag_server_route(payload: ProbeLightRagServerRequest):
    """Test-connect to an external LightRAG server before binding a KB to it.

    Returns the verdict (reachable? is it a LightRAG server? API key accepted?)
    so the UI can confirm before any registration happens. Creates nothing.
    """
    from traittutor.services.rag.pipelines.lightrag_server.probe import probe_server

    server_url = (payload.server_url or "").strip()
    if not server_url:
        raise HTTPException(status_code=400, detail="server_url is required.")
    result = await probe_server(server_url, payload.api_key or "")
    return result.to_dict()


@router.post("/connect-lightrag-server")
async def connect_lightrag_server_route(payload: ConnectLightRagServerRequest):
    """Connect an external LightRAG server as a retrieval-only knowledge base.

    Re-probes server-side (never trusts the client's verdict), then registers a
    pointer (``type: lightrag_server``). Retrieval is offloaded to the server's
    ``/query`` endpoint — no copy, no local index.
    """
    from traittutor.services.rag.pipelines.lightrag_server.config import SUPPORTED_MODES
    from traittutor.services.rag.pipelines.lightrag_server.probe import probe_server

    name = (payload.name or "").strip()
    server_url = (payload.server_url or "").strip()
    if not name or not server_url:
        raise HTTPException(status_code=400, detail="Both name and server_url are required.")

    result = await probe_server(server_url, payload.api_key or "")
    if not result.ok:
        raise HTTPException(
            status_code=400, detail=result.error or "Could not connect to the LightRAG server."
        )

    search_mode = (payload.search_mode or "").strip().lower()
    if search_mode and search_mode not in SUPPORTED_MODES:
        search_mode = ""

    try:
        manager = get_kb_manager()
        entry = manager.register_lightrag_server_kb(
            name,
            result.base_url,
            api_key=payload.api_key or "",
            search_mode=search_mode,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error connecting LightRAG server: {e}")
        raise HTTPException(status_code=500, detail="Internal server error") from e

    return {
        "status": "connected",
        "name": name,
        "server_url": entry["server_url"],
        "rag_provider": entry["rag_provider"],
    }


@router.post("/{kb_name}/link-folder", response_model=LinkedFolderInfo)
async def link_folder(kb_name: str, request: LinkFolderRequest):
    """
    Link a local folder to a knowledge base.

    This allows syncing documents from a local folder (which can be
    synced with SharePoint, Google Drive, OneLake, etc.) to the KB.

    The folder path supports:
    - Absolute paths: /Users/name/Documents or C:\\Users\\name\\Documents
    - Home directory: ~/Documents
    - Relative paths (resolved from server working directory)
    """
    try:
        manager, resolved_name, _ = _writable_kb(kb_name)
        _assert_not_connected_kb(resolved_name, _load_kb_entry_or_404(manager, resolved_name))
        folder_info = manager.link_folder(resolved_name, request.folder_path)
        logger.info(f"Linked folder '{request.folder_path}' to KB '{kb_name}'")
        return LinkedFolderInfo(**folder_info)
    except HTTPException:
        raise
    except ValueError as e:
        error_msg = str(e)
        if "not found" in error_msg.lower():
            raise HTTPException(status_code=404, detail=error_msg) from e
        raise HTTPException(status_code=400, detail=error_msg) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.get("/{kb_name}/linked-folders", response_model=list[LinkedFolderInfo])
async def get_linked_folders(kb_name: str):
    """Get list of linked folders for a knowledge base."""
    try:
        resource = resolve_kb(kb_name)
        manager = manager_for_resource(resource)
        folders = manager.get_linked_folders(resource.name)
        return [LinkedFolderInfo(**f) for f in folders]
    except HTTPException:
        raise
    except ValueError:
        raise HTTPException(
            status_code=404, detail=f"Knowledge base '{kb_name}' not found"
        ) from None
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.delete("/{kb_name}/linked-folders/{folder_id}")
async def unlink_folder(kb_name: str, folder_id: str):
    """Unlink a folder from a knowledge base."""
    try:
        manager, resolved_name, _ = _writable_kb(kb_name)
        success = manager.unlink_folder(resolved_name, folder_id)
        if not success:
            raise HTTPException(status_code=404, detail=f"Folder '{folder_id}' not found")
        logger.info(f"Unlinked folder '{folder_id}' from KB '{kb_name}'")
        return {"message": "Folder unlinked successfully", "folder_id": folder_id}
    except HTTPException:
        raise
    except ValueError:
        raise HTTPException(
            status_code=404, detail=f"Knowledge base '{kb_name}' not found"
        ) from None
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.post("/{kb_name}/sync-folder/{folder_id}")
async def sync_folder(kb_name: str, folder_id: str, background_tasks: BackgroundTasks):
    """
    Sync files from a linked folder to the knowledge base.

    This scans the linked folder for supported documents and processes
    any new files that haven't been added yet.
    """
    try:
        manager, kb_name, kb_base_dir = _writable_kb(kb_name)
        kb_entry = _load_kb_entry_or_404(manager, kb_name)
        _assert_kb_writable_or_409(kb_name, kb_entry)
        kb_provider = _validate_registered_provider(
            kb_entry.get("rag_provider") or DEFAULT_PROVIDER
        )

        # Get linked folders and find the one with matching ID
        folders = manager.get_linked_folders(kb_name)
        folder_info = next((f for f in folders if f["id"] == folder_id), None)

        if not folder_info:
            raise HTTPException(status_code=404, detail=f"Linked folder '{folder_id}' not found")

        folder_path = folder_info["path"]

        # Check for changes (new or modified files)
        changes = manager.detect_folder_changes(kb_name, folder_id)
        files_to_process = changes["new_files"] + changes["modified_files"]

        if not files_to_process:
            return {"message": "No new or modified files to sync", "files": [], "file_count": 0}

        logger.info(
            f"Syncing {len(files_to_process)} files from folder '{folder_path}' to KB '{kb_name}'"
        )
        task_id = _build_unique_task_id("kb_upload", f"{kb_name}_folder_{folder_id}")
        get_task_stream_manager().ensure_task(task_id)

        # NOTE: We DO NOT update sync state here anymore.
        # It is updated in run_upload_processing_task only after successful processing.
        # This prevents marking files as synced if processing fails (race condition fix).

        _mark_kb_queued_for_processing(
            manager,
            kb_name,
            task_id,
            f"Syncing {len(files_to_process)} file(s) from linked folder...",
        )

        # Add background task to process files
        background_tasks.add_task(
            run_upload_processing_task,
            kb_name=kb_name,
            base_dir=str(kb_base_dir),
            uploaded_file_paths=files_to_process,
            task_id=task_id,
            rag_provider=kb_provider,
            folder_id=folder_id,  # Pass folder_id to update state on success
        )

        return {
            "message": f"Syncing {len(files_to_process)} files from linked folder",
            "folder_path": folder_path,
            "new_files": changes["new_count"],
            "modified_files": changes["modified_count"],
            "file_count": len(files_to_process),
            "task_id": task_id,
        }
    except HTTPException:
        raise
    except ValueError:
        raise HTTPException(
            status_code=404, detail=f"Knowledge base '{kb_name}' not found"
        ) from None
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error") from e
