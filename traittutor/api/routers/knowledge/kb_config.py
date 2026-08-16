"""
Knowledge Base API Router
=========================

Handles knowledge base CRUD operations, file uploads, and initialization.
"""

from datetime import datetime
import traceback

from fastapi import (
    APIRouter,
    HTTPException,
)

# Initialize logger with config
from traittutor.api.routers.knowledge._shared import (  # noqa: F401
    KBConfigUpdateRequest,
    KnowledgeBaseInfo,
    _assert_not_connected_kb,
    _current_kb_base_dir,
    _validate_registered_provider,
    _writable_kb,
    get_kb_manager,
)
from traittutor.api.routers.knowledge._shared import _shared_logger as logger
from traittutor.knowledge.state_store import KnowledgeStateStore
from traittutor.multi_user.context import get_current_user
from traittutor.multi_user.knowledge_access import (
    list_visible_knowledge_bases as list_visible_kb_access,
)
from traittutor.multi_user.knowledge_access import (
    manager_for_resource,
    resolve_kb,
)

router = APIRouter()


@router.get("/configs")
async def get_all_kb_configs():
    """Get all canonical knowledge-base configurations."""
    try:
        return KnowledgeStateStore(_current_kb_base_dir()).load_config()
    except Exception as e:
        logger.error(f"Error getting KB configs: {e}")
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.get("/{kb_name}/config")
async def get_kb_config(kb_name: str):
    """Get configuration for a specific knowledge base."""
    try:
        state = KnowledgeStateStore(_current_kb_base_dir()).load_config()
        defaults = dict(state.get("defaults", {}))
        stored = state.get("knowledge_bases", {}).get(kb_name)
        if not isinstance(stored, dict):
            raise HTTPException(status_code=404, detail="Knowledge base not found")
        config = {**defaults, **stored}
        return {"kb_name": kb_name, "config": config}
    except Exception as e:
        logger.error(f"Error getting config for KB '{kb_name}': {e}")
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.put("/{kb_name}/config")
async def update_kb_config(kb_name: str, request: KBConfigUpdateRequest):
    """Update configuration for a specific knowledge base (typed whitelist).

    Only provider selection is editable through this generic route:
    ``status``/``progress``/``needs_reindex`` belong to the internal task
    pipeline, and filesystem pointer fields (``type``/``external_path``/
    ``vault_path``/``storage_path``) are only set by the dedicated
    connect-folder / connect-obsidian flows, which enforce the path
    allowlist. Unknown fields fail explicitly per the typed-protocol
    contract. No in-repo consumer currently calls this route; it is kept
    for API compatibility and hardened after a review finding that the
    previous bare-dict merge could re-point a KB at an arbitrary directory.
    """
    try:
        from traittutor.services.rag.index_probe import has_ready_provider_index

        state_store = KnowledgeStateStore(_current_kb_base_dir())
        state = state_store.load_config()
        knowledge_bases = state.setdefault("knowledge_bases", {})
        current_entry = knowledge_bases.get(kb_name)
        if not isinstance(current_entry, dict):
            raise HTTPException(status_code=404, detail="Knowledge base not found")
        # Connected KBs (Obsidian vaults, linked indexes) are read-only
        # pointers; every other KB write endpoint enforces the same guard.
        _assert_not_connected_kb(kb_name, current_entry)

        config: dict[str, object] = {}
        if request.rag_provider is not None:
            requested_provider = _validate_registered_provider(request.rag_provider)
            current_provider = _validate_registered_provider(current_entry.get("rag_provider"))
            if requested_provider != current_provider:
                kb_dir = _current_kb_base_dir() / kb_name
                if kb_dir.exists() and has_ready_provider_index(kb_dir, current_provider):
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            f"Knowledge base '{kb_name}' already has a ready "
                            f"{current_provider} index. Provider changes require "
                            "an explicit re-index/migration instead of a silent config edit."
                        ),
                    )
                config["needs_reindex"] = True
                config["status"] = "needs_reindex"
                config["progress"] = {
                    "stage": "needs_reindex",
                    "message": (
                        f"Provider changed from {current_provider} to {requested_provider}; "
                        "re-index this knowledge base before use."
                    ),
                    "percent": 0,
                    "timestamp": datetime.now().isoformat(),
                }
            config["rag_provider"] = requested_provider
        if not config:
            raise HTTPException(
                status_code=422,
                detail="No editable config fields provided; supported field: rag_provider.",
            )
        current_entry.update(config)
        state_store.save_config(state)
        return {"status": "success", "kb_name": kb_name, "config": dict(current_entry)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating config for KB '{kb_name}': {e}")
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.get("/default")
async def get_default_kb():
    """Get the default knowledge base."""
    try:
        manager = get_kb_manager()
        default_kb = manager.get_default()
        return {"default_kb": default_kb}
    except Exception as e:
        logger.error(f"Error getting default KB: {e}")
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.put("/default/{kb_name}")
async def set_default_kb(kb_name: str):
    """Set the default knowledge base."""
    try:
        manager, kb_name, _ = _writable_kb(kb_name)

        # Verify KB exists
        if kb_name not in manager.list_knowledge_bases():
            raise HTTPException(status_code=404, detail=f"Knowledge base '{kb_name}' not found")

        manager.set_default(kb_name)
        return {"status": "success", "default_kb": kb_name}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error setting default KB: {e}")
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.get("/list", response_model=list[KnowledgeBaseInfo])
async def list_knowledge_bases():
    """List all available knowledge bases with their details."""
    try:
        manager = get_kb_manager()
        kb_names = manager.list_knowledge_bases()
        access_items = list_visible_kb_access()
        access_by_id = {str(item.get("id") or ""): item for item in access_items}
        own_prefix = "admin:kb:" if get_current_user().is_admin else "user:kb:"

        logger.debug(f"Found {len(kb_names)} knowledge bases: {kb_names}")

        result = []
        errors = []

        for name in kb_names:
            try:
                info = manager.get_info(name)
                logger.debug(f"Successfully got info for KB '{name}': {info.get('statistics', {})}")
                result.append(
                    KnowledgeBaseInfo(
                        id=f"{own_prefix}{info['name']}",
                        name=info["name"],
                        is_default=info["is_default"],
                        statistics=info.get("statistics", {}),
                        metadata=info.get("metadata"),
                        path=info.get("path"),
                        status=info.get("status"),
                        progress=info.get("progress"),
                        source="admin" if get_current_user().is_admin else "user",
                        assigned=False,
                        read_only=False,
                        provenance_label=access_by_id.get(f"{own_prefix}{info['name']}", {}).get(
                            "provenance_label"
                        ),
                    )
                )
            except Exception as e:
                error_msg = f"Error getting info for KB '{name}': {e}"
                errors.append(error_msg)
                logger.warning(f"{error_msg}\n{traceback.format_exc()}")
                try:
                    kb_dir = manager.base_dir / name
                    if kb_dir.exists():
                        logger.debug(f"KB '{name}' directory exists, creating error fallback info")
                        fallback_progress = {
                            "stage": "error",
                            "message": "Failed to load knowledge base info.",
                            "error": error_msg,
                        }
                        result.append(
                            KnowledgeBaseInfo(
                                id=f"{own_prefix}{name}",
                                name=name,
                                is_default=name == manager.get_default(),
                                statistics={
                                    "raw_documents": 0,
                                    "images": 0,
                                    "content_lists": 0,
                                    "rag_initialized": False,
                                },
                                metadata={"name": name, "last_error": error_msg},
                                path=str(kb_dir),
                                status="error",
                                progress=fallback_progress,
                                source="admin" if get_current_user().is_admin else "user",
                            )
                        )
                except Exception as fallback_err:
                    logger.error(f"Fallback also failed for KB '{name}': {fallback_err}")

        if errors and not result:
            error_detail = f"Failed to load knowledge bases. Errors: {'; '.join(errors)}"
            logger.error(error_detail)
            raise HTTPException(status_code=500, detail=error_detail)

        if errors:
            logger.warning(
                f"Some KBs had errors, returning {len(result)} results. Errors: {errors}"
            )

        logger.debug(f"Returning {len(result)} knowledge bases")
        if not get_current_user().is_admin:
            own_ids = {item.id for item in result}
            for access in access_items:
                if access.get("source") != "admin" or access.get("id") in own_ids:
                    continue
                if not access.get("available", True):
                    result.append(
                        KnowledgeBaseInfo(
                            id=str(access.get("id") or ""),
                            name=str(access.get("name") or ""),
                            is_default=False,
                            statistics={},
                            metadata={},
                            path=None,
                            status="unavailable",
                            progress=None,
                            source="admin",
                            assigned=True,
                            read_only=True,
                            provenance_label=str(access.get("provenance_label") or ""),
                            available=False,
                        )
                    )
                    continue
                resource = resolve_kb(str(access.get("id") or access.get("name") or ""))
                assigned_manager = manager_for_resource(resource)
                try:
                    info = assigned_manager.get_info(resource.name)
                    result.append(
                        KnowledgeBaseInfo(
                            id=resource.id,
                            name=info["name"],
                            is_default=False,
                            statistics=info.get("statistics", {}),
                            metadata=info.get("metadata"),
                            path=None,
                            status=info.get("status"),
                            progress=info.get("progress"),
                            source="admin",
                            assigned=True,
                            read_only=True,
                            provenance_label=str(access.get("provenance_label") or ""),
                        )
                    )
                except Exception as exc:
                    error_msg = f"Error getting assigned KB '{resource.name}': {exc}"
                    result.append(
                        KnowledgeBaseInfo(
                            id=resource.id,
                            name=resource.name,
                            is_default=False,
                            statistics={},
                            metadata={"name": resource.name, "last_error": error_msg},
                            status="error",
                            progress={
                                "stage": "error",
                                "message": "Failed to load assigned knowledge base info.",
                                "error": error_msg,
                            },
                            source="admin",
                            assigned=True,
                            read_only=True,
                            provenance_label=str(access.get("provenance_label") or ""),
                        )
                    )
        return result
    except HTTPException:
        raise
    except Exception as e:
        error_msg = f"Error listing knowledge bases: {e}"
        logger.error(f"{error_msg}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Failed to list knowledge bases: {e!s}") from e


@router.get("/{kb_name}")
async def get_knowledge_base_details(kb_name: str):
    """Get detailed info for a specific KB."""
    try:
        resource = resolve_kb(kb_name)
        manager = manager_for_resource(resource)
        info = manager.get_info(resource.name)
        info.update(
            {
                "id": resource.id,
                "source": resource.source,
                "assigned": resource.assigned,
                "read_only": resource.read_only,
            }
        )
        if resource.assigned:
            info.pop("path", None)
        return info
    except HTTPException:
        raise
    except ValueError:
        raise HTTPException(
            status_code=404, detail=f"Knowledge base '{kb_name}' not found"
        ) from None
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.delete("/{kb_name}")
async def delete_knowledge_base(kb_name: str):
    """Delete a knowledge base."""
    try:
        manager, resolved_name, _ = _writable_kb(kb_name)
        success = manager.delete_knowledge_base(resolved_name, confirm=True)
        if not success:
            raise HTTPException(status_code=400, detail="Failed to delete knowledge base")
        logger.info(f"KB '{kb_name}' deleted")
        return {"message": f"Knowledge base '{kb_name}' deleted successfully"}
    except ValueError:
        raise HTTPException(
            status_code=404, detail=f"Knowledge base '{kb_name}' not found"
        ) from None
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error") from e
