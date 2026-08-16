"""
Knowledge Base API Router
=========================

Handles knowledge base CRUD operations, file uploads, and initialization.
"""

import traceback
from typing import Any

from fastapi import (
    APIRouter,
    HTTPException,
)

# Initialize logger with config
from traittutor.api.routers.knowledge._shared import (  # noqa: F401
    _ENGINE_MODEL_KINDS,
    IMAGE_ACCEPT_MIME_TYPES,
    ActiveModelUpdate,
    GraphRagConfigUpdate,
    LightRagConfigUpdate,
    LlamaIndexConfigUpdate,
    PageIndexConfigUpdate,
    ProviderModeUpdate,
    SupportedFileTypesInfo,
    _current_kb_base_dir,
    get_kb_manager,
)
from traittutor.api.routers.knowledge._shared import _shared_logger as logger
from traittutor.knowledge.state_store import KnowledgeStateStore
from traittutor.services.rag.file_routing import FileTypeRouter
from traittutor.services.rag.linked_kb import (
    LINKABLE_PROVIDERS,
)
from traittutor.utils.document_validator import DocumentValidator

router = APIRouter()


@router.get("/health")
async def health_check():
    """Health check endpoint.

    Failure details (exception text, stack trace, filesystem locations) stay
    in the server log; clients only learn that the subsystem is unhealthy.
    """
    try:
        manager = get_kb_manager()
        kb_count = len(manager.list_knowledge_bases())
        return {
            "status": "ok",
            "state_backend": "sqlite",
            "knowledge_bases_count": kb_count,
        }
    except Exception as e:
        logger.error(f"Knowledge health check failed: {e}\n{traceback.format_exc()}")
        return {"status": "error"}


@router.get("/rag-providers")
async def get_rag_providers():
    """Get list of available RAG providers (with the active per-engine mode)."""
    try:
        from traittutor.services.rag.service import RAGService

        providers = RAGService.list_providers()
        config = KnowledgeStateStore(_current_kb_base_dir()).load_config()
        provider_modes = config.get("defaults", {}).get("provider_modes", {})
        for provider in providers:
            modes = provider.get("modes") or []
            if modes:
                stored = provider_modes.get(provider["id"])
                if stored in modes:
                    provider["default_mode"] = stored
            # Whether an existing index for this engine can be linked in place
            # (self-contained on disk). Drives the "link existing folder" UI.
            provider["linkable"] = provider.get("id") in LINKABLE_PROVIDERS
        return {"providers": providers}
    except Exception as e:
        logger.error(f"Error getting RAG providers: {e}")
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.put("/rag-providers/{provider}/mode")
async def set_rag_provider_mode(provider: str, payload: ProviderModeUpdate):
    """Persist the default retrieval mode for a mode-aware engine.

    The mode must be one the engine supports; a KB's own ``search_mode`` still
    overrides this per-KB default.
    """
    from traittutor.services.rag.service import RAGService

    entry = next((p for p in RAGService.list_providers() if p["id"] == provider), None)
    modes_raw: Any = (entry or {}).get("modes") or []
    modes: list[str] = [str(value) for value in modes_raw] if isinstance(modes_raw, list) else []
    if entry is None or not modes:
        raise HTTPException(status_code=404, detail=f"No retrieval modes for engine '{provider}'.")

    mode = (payload.mode or "").strip().lower()
    if mode not in modes:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid mode '{payload.mode}' for {provider}. Choose one of: {', '.join(modes)}.",
        )

    state = KnowledgeStateStore(_current_kb_base_dir())
    config = state.load_config()
    config.setdefault("defaults", {}).setdefault("provider_modes", {})[provider] = mode
    state.save_config(config)
    return {"provider": provider, "mode": mode}


def _pageindex_config_payload() -> dict:
    """PageIndex pipeline settings for the UI, with the API key redacted."""
    from traittutor.services.config import get_runtime_settings_service

    settings = get_runtime_settings_service().load_pageindex()
    return {
        "api_base_url": settings.get("api_base_url") or "",
        "api_key_set": bool(settings.get("api_key")),
        "configured": bool(settings.get("api_key")),
    }


@router.get("/rag-pipelines/pageindex/config")
async def get_pageindex_pipeline_config():
    """Read the PageIndex credential state (key redacted to a boolean)."""
    try:
        return _pageindex_config_payload()
    except Exception as e:
        logger.error(f"Error reading PageIndex config: {e}")
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.put("/rag-pipelines/pageindex/config")
async def update_pageindex_pipeline_config(payload: PageIndexConfigUpdate):
    """Persist the PageIndex API key / base URL for this user's account."""
    try:
        from traittutor.services.config import get_runtime_settings_service
        from traittutor.services.rag.pipelines.pageindex.config import DEFAULT_API_BASE_URL

        service = get_runtime_settings_service()
        current = service.load_pageindex(include_process_overrides=False)

        api_key = current.get("api_key", "")
        if payload.api_key is not None:
            api_key = payload.api_key.strip()

        api_base_url = current.get("api_base_url") or DEFAULT_API_BASE_URL
        if payload.api_base_url is not None and payload.api_base_url.strip():
            api_base_url = payload.api_base_url.strip()

        service.save_pageindex({"api_key": api_key, "api_base_url": api_base_url})

        # The built-in pageindex MCP server derives its URL/Bearer header from
        # these settings — resync connections so key changes apply immediately.
        try:
            from traittutor.services.mcp import get_mcp_manager

            await get_mcp_manager().reload()
        except Exception:
            logger.warning("MCP reload after PageIndex config change failed", exc_info=True)

        return _pageindex_config_payload()
    except Exception as e:
        logger.error(f"Error updating PageIndex config: {e}")
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.get("/rag-pipelines/llamaindex/config")
async def get_llamaindex_pipeline_config():
    """Read the LlamaIndex engine's retrieval + chunking knobs."""
    try:
        from traittutor.services.config import get_runtime_settings_service

        return get_runtime_settings_service().load_llamaindex()
    except Exception as e:
        logger.error(f"Error reading LlamaIndex config: {e}")
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.put("/rag-pipelines/llamaindex/config")
async def update_llamaindex_pipeline_config(payload: LlamaIndexConfigUpdate):
    """Persist the LlamaIndex engine knobs.

    Retrieval knobs take effect on the next query; chunk geometry only changes
    how documents indexed *after* the save are split.
    """
    try:
        from traittutor.services.config import get_runtime_settings_service

        service = get_runtime_settings_service()
        current = service.load_llamaindex(include_process_overrides=False)
        # Merge only the provided fields so partial saves never wipe others.
        updates = payload.model_dump(exclude_none=True)
        return service.save_llamaindex({**current, **updates})
    except Exception as e:
        logger.error(f"Error updating LlamaIndex config: {e}")
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.get("/rag-pipelines/graphrag/config")
async def get_graphrag_pipeline_config():
    """Read GraphRAG's query knobs (response style, community granularity)."""
    try:
        from traittutor.services.config import get_runtime_settings_service

        return get_runtime_settings_service().load_graphrag()
    except Exception as e:
        logger.error(f"Error reading GraphRAG config: {e}")
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.put("/rag-pipelines/graphrag/config")
async def update_graphrag_pipeline_config(payload: GraphRagConfigUpdate):
    """Persist GraphRAG's query knobs. Takes effect on the next query."""
    try:
        from traittutor.services.config import get_runtime_settings_service

        service = get_runtime_settings_service()
        current = service.load_graphrag()
        updates = payload.model_dump(exclude_none=True)
        return service.save_graphrag({**current, **updates})
    except Exception as e:
        logger.error(f"Error updating GraphRAG config: {e}")
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.get("/rag-pipelines/lightrag/config")
async def get_lightrag_pipeline_config():
    """Read LightRAG's query knobs (top_k, response style)."""
    try:
        from traittutor.services.config import get_runtime_settings_service

        return get_runtime_settings_service().load_lightrag()
    except Exception as e:
        logger.error(f"Error reading LightRAG config: {e}")
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.put("/rag-pipelines/lightrag/config")
async def update_lightrag_pipeline_config(payload: LightRagConfigUpdate):
    """Persist LightRAG's query knobs. Takes effect on the next query."""
    try:
        from traittutor.services.config import get_runtime_settings_service

        service = get_runtime_settings_service()
        current = service.load_lightrag()
        updates = payload.model_dump(exclude_none=True)
        return service.save_lightrag({**current, **updates})
    except Exception as e:
        logger.error(f"Error updating LightRAG config: {e}")
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.get("/rag-pipelines/{provider}/preflight")
async def get_rag_pipeline_preflight(provider: str):
    """Check whether ``provider`` can run in the current environment.

    Returns ``{ok, checks:[{key,label,ok,detail,optional}]}`` — package
    install, API key, and active model requirements per engine.
    """
    try:
        from traittutor.services.rag.preflight import engine_preflight

        return engine_preflight(provider)
    except Exception as e:
        logger.error(f"Error running preflight for '{provider}': {e}")
        raise HTTPException(status_code=500, detail="Internal server error") from e


def _model_options_payload(kinds: list[str]) -> dict:
    """Secret-free model options per kind for the engine page picker.

    Exposes only ids / display labels / dimensions — never provider URLs or
    API keys (those stay behind the admin-only catalog endpoint).
    """
    from traittutor.services.config import get_model_catalog_service

    catalog = get_model_catalog_service().load()
    services = catalog.get("services", {})
    out: dict = {}
    for kind in kinds:
        svc = services.get(kind) or {}
        options = []
        for profile in svc.get("profiles", []) or []:
            pid = profile.get("id")
            pname = profile.get("name") or pid
            for model in profile.get("models", []) or []:
                detail = ""
                if kind == "embedding" and model.get("dimension"):
                    detail = f"{model.get('dimension')}d"
                options.append(
                    {
                        "profile_id": pid,
                        "profile_name": pname,
                        "model_id": model.get("id"),
                        "label": model.get("name") or model.get("model") or model.get("id"),
                        "model": model.get("model") or "",
                        "detail": detail,
                    }
                )
        out[kind] = {
            "active": {
                "profile_id": svc.get("active_profile_id"),
                "model_id": svc.get("active_model_id"),
            },
            "options": options,
        }
    return out


@router.get("/rag-pipelines/model-options")
async def get_rag_model_options(kinds: str = "llm,embedding"):
    """List configured models (secret-free) for the requested model kinds."""
    try:
        requested = [
            k.strip() for k in kinds.split(",") if k.strip() in _ENGINE_MODEL_KINDS
        ] or list(_ENGINE_MODEL_KINDS)
        return _model_options_payload(requested)
    except Exception as e:
        logger.error(f"Error reading model options: {e}")
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.put("/rag-pipelines/active-model")
async def set_rag_active_model(payload: ActiveModelUpdate):
    """Set the active model for an engine's required kind, applied immediately.

    This is the same active selection the model catalog manages; switching it
    here affects every engine that uses that kind (the active model is global).
    """
    if payload.kind not in _ENGINE_MODEL_KINDS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported model kind '{payload.kind}'. Choose one of: {', '.join(_ENGINE_MODEL_KINDS)}.",
        )
    try:
        from traittutor.services.config import get_model_catalog_service

        service = get_model_catalog_service()
        catalog = service.load()
        svc = (catalog.get("services") or {}).get(payload.kind)
        if not svc:
            raise HTTPException(status_code=404, detail=f"No '{payload.kind}' models configured.")
        profile = next(
            (p for p in svc.get("profiles", []) if p.get("id") == payload.profile_id), None
        )
        if profile is None:
            raise HTTPException(status_code=400, detail="Unknown profile for this kind.")
        if not any(m.get("id") == payload.model_id for m in profile.get("models", [])):
            raise HTTPException(status_code=400, detail="Unknown model for this profile.")
        svc["active_profile_id"] = payload.profile_id
        svc["active_model_id"] = payload.model_id
        service.apply(catalog)
        return _model_options_payload([payload.kind])[payload.kind]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error setting active model: {e}")
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.get("/supported-file-types", response_model=SupportedFileTypesInfo)
async def get_supported_file_types():
    """Return the current upload policy so the web client stays in sync."""
    extensions = sorted(FileTypeRouter.get_supported_extensions())
    accept_items = extensions + [
        mime
        for extension, mime in sorted(IMAGE_ACCEPT_MIME_TYPES.items())
        if extension in FileTypeRouter.IMAGE_EXTENSIONS
    ]
    return SupportedFileTypesInfo(
        extensions=extensions,
        accept=",".join(dict.fromkeys(accept_items)),
        max_file_size_bytes=DocumentValidator.MAX_FILE_SIZE,
    )
