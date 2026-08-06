from contextlib import asynccontextmanager
import logging
import sys

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from traittutor.logging import configure_logging
from traittutor.services.config import (
    ensure_runtime_settings_files,
    export_runtime_settings_to_env,
    load_auth_settings,
    load_system_settings,
)
from traittutor.services.config.origins import normalize_origins

ensure_runtime_settings_files()
export_runtime_settings_to_env(overwrite=True)
configure_logging()
logger = logging.getLogger(__name__)


class _SuppressWsNoise(logging.Filter):
    """Suppress noisy uvicorn logs for WebSocket connection churn."""

    _SUPPRESSED = ("connection open", "connection closed")

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return not any(f in msg for f in self._SUPPRESSED)


logging.getLogger("uvicorn.error").addFilter(_SuppressWsNoise())

CONFIG_DRIFT_ERROR_TEMPLATE = (
    "Configuration Drift Detected: Capability tool references {drift} are not "
    "registered in the runtime tool registry. Register the missing tools or "
    "remove the stale tool names from the capability manifests."
)


def validate_tool_consistency():
    """
    Validate that capability manifests only reference tools that are actually
    registered in the runtime ``ToolRegistry``.
    """
    try:
        from traittutor.runtime.registry.capability_registry import get_capability_registry
        from traittutor.runtime.registry.tool_registry import get_tool_registry

        capability_registry = get_capability_registry()
        tool_registry = get_tool_registry()
        available_tools = set(tool_registry.list_tools())

        referenced_tools = set()
        for manifest in capability_registry.get_manifests():
            referenced_tools.update(manifest.get("tools_used", []) or [])

        drift = referenced_tools - available_tools
        if drift:
            raise RuntimeError(CONFIG_DRIFT_ERROR_TEMPLATE.format(drift=drift))
    except RuntimeError:
        logger.exception("Configuration validation failed")
        raise
    except Exception:
        logger.exception("Failed to load configuration for validation")
        raise


def _build_cors_settings() -> dict[str, object]:
    """Build CORS settings for both localhost and remote Docker deployments."""
    system_settings = load_system_settings()
    auth_settings = load_auth_settings()
    frontend_port = str(system_settings["frontend_port"])
    extra_origins = normalize_origins(
        [system_settings["cors_origin"], system_settings["cors_origins"]]
    )
    origins = [
        f"http://localhost:{frontend_port}",
        f"http://127.0.0.1:{frontend_port}",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]
    for origin in extra_origins:
        if origin not in origins:
            origins.append(origin)

    # Auth is enabled by default. When an operator explicitly disables it for
    # local/single-user mode, mirror the pre-v1.3.8 behavior and allow remote
    # Docker/LAN origins out of the box. When auth is enabled (the default),
    # require explicit CORS_ORIGIN(S) for credentialed cross-origin requests.
    allow_origin_regex = None if auth_settings["enabled"] else r"https?://.*"
    mode = "explicit" if auth_settings["enabled"] else "permissive"
    return {
        "allow_origins": origins,
        "allow_origin_regex": allow_origin_regex,
        "mode": mode,
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifecycle management
    Gracefully handle startup and shutdown events, avoid CancelledError
    """
    # Execute on startup
    logger.info("Application startup")

    # Validate configuration consistency
    validate_tool_consistency()

    # Initialize LLM client early so OPENAI_* env vars are available before
    # any downstream provider integrations start.
    try:
        from traittutor.services.llm import get_llm_client

        llm_client = get_llm_client()
        logger.info(f"LLM client initialized: model={llm_client.config.model}")
    except Exception as e:
        logger.warning(f"Failed to initialize LLM client at startup: {e}")

    try:
        from traittutor.events.event_bus import get_event_bus

        event_bus = get_event_bus()
        await event_bus.start()
        logger.info("EventBus started")
    except Exception as e:
        logger.warning(f"Failed to start EventBus: {e}")

    # Recover durable generation coordination before accepting requests. Any
    # lease left by a prior process is marked interrupted/retryable, while
    # queued work is atomically claimed by whichever API instance has capacity.
    try:
        from traittutor.generate.tasks import get_generation_task_manager

        await get_generation_task_manager().start()
    except Exception as e:
        logger.warning(f"Failed to recover generation task queue: {e}")

    # Ping PocketBase if configured — logs a warning (not an error) if unreachable
    try:
        from traittutor.services.pocketbase_client import ping_pocketbase

        await ping_pocketbase()
    except Exception as e:
        logger.warning(f"PocketBase startup check failed: {e}")

    # Migrate any v1 memory files (PROFILE.md / SUMMARY.md) into a
    # backup folder so the v2 three-layer subsystem starts clean.
    try:
        from traittutor.services.memory import (
            migrate_partner_surface_if_needed,
            migrate_v1_if_needed,
        )

        backup = migrate_v1_if_needed()
        if backup is not None:
            logger.info("v1 memory archived to %s", backup)
        # Rename an earlier companion memory surface to the current archived
        # conversation surface so older local data remains readable.
        migrate_partner_surface_if_needed()
    except Exception as e:
        logger.warning(f"v1 memory migration failed: {e}")

    yield

    # Execute on shutdown
    logger.info("Application shutdown")

    # Stop EventBus
    try:
        from traittutor.events.event_bus import get_event_bus

        event_bus = get_event_bus()
        await event_bus.stop()
        logger.info("EventBus stopped")
    except Exception as e:
        logger.warning(f"Failed to stop EventBus: {e}")


app = FastAPI(
    title="TraitTutor API",
    version="1.0.0",
    lifespan=lifespan,
    # Disable automatic trailing slash redirects to prevent protocol downgrade issues
    # when deployed behind HTTPS reverse proxies (e.g., nginx).
    # Without this, FastAPI's 307 redirects may change HTTPS to HTTP.
    redirect_slashes=False,
)

# Access logging is funneled through this one middleware. uvicorn's own
# per-request access log is disabled on every launch path (run_server.py via
# access_log=False; the launcher and Docker via `--no-access-log`), so routine
# 200s — the chatty frontend polling of /settings, /tools, /knowledge/list,
# etc. — never reach the logs. Only non-200s are surfaced, since those are the
# ones worth seeing.
#
# The `traittutor.access` logger gets its own INFO stdout handler rather than
# leaning on the root handlers: the root console handler runs at the global log
# level (WARNING by default), which would swallow these INFO access lines.
# propagate=False keeps them from also printing through root if the global
# level is ever lowered to INFO/DEBUG.
_access_logger = logging.getLogger("traittutor.access")
if not any(getattr(h, "_traittutor_access_handler", False) for h in _access_logger.handlers):
    _access_handler = logging.StreamHandler(sys.stdout)
    _access_handler.setLevel(logging.INFO)
    _access_handler.setFormatter(logging.Formatter("%(message)s"))
    _access_handler._traittutor_access_handler = True  # type: ignore[attr-defined]
    _access_logger.addHandler(_access_handler)
    _access_logger.setLevel(logging.INFO)
    _access_logger.propagate = False


@app.middleware("http")
async def selective_access_log(request, call_next):
    response = await call_next(request)
    if response.status_code != 200:
        _access_logger.info(
            '%s - "%s %s HTTP/%s" %d',
            request.client.host if request.client else "-",
            request.method,
            request.url.path,
            request.scope.get("http_version", "1.1"),
            response.status_code,
        )
    return response


_cors_settings = _build_cors_settings()
logger.info(
    "CORS configured: mode=%s allow_origins=%s allow_origin_regex=%s",
    _cors_settings["mode"],
    _cors_settings["allow_origins"],
    _cors_settings["allow_origin_regex"],
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_settings["allow_origins"],
    allow_origin_regex=_cors_settings["allow_origin_regex"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Import routers only after runtime settings are initialized.
# Some router modules load YAML settings at import time.
from traittutor.api.routers import (
    admin,
    attachments,
    auth,
    capabilities_settings,
    chat,
    dashboard,
    knowledge,
    learning_packs,
    mcp_settings,
    memory,
    notebook,
    outputs,
    personas,
    question,
    question_notebook,
    quiz_judge,
    sessions,
    settings,
    system,
    traittutor_generate,
    traittutor_profile,
    personalization,
    unified_ws,
    voice,
)
from traittutor.api.routers import (
    tools as tools_router,
)

# Auth router is public — login/logout/register/status require no token
app.include_router(auth.router, prefix="/api/v1/auth", tags=["auth"])

# All other routers require a valid session when AUTH_ENABLED=true.
# require_auth is a no-op when AUTH_ENABLED=false, so this is safe for local use.
from traittutor.api.routers.auth import require_auth  # noqa: E402

_auth = [Depends(require_auth)]
app.include_router(admin.router, prefix="/api/v1/admin", tags=["admin"])
app.include_router(chat.router, prefix="/api/v1", tags=["chat"], dependencies=_auth)
app.include_router(
    question.router, prefix="/api/v1/question", tags=["question"], dependencies=_auth
)
app.include_router(
    knowledge.router, prefix="/api/v1/knowledge", tags=["knowledge"], dependencies=_auth
)
app.include_router(
    dashboard.router, prefix="/api/v1/dashboard", tags=["dashboard"], dependencies=_auth
)
app.include_router(
    learning_packs.router,
    prefix="/api/v1/learning-packs",
    tags=["learning-packs"],
    dependencies=_auth,
)
app.include_router(
    notebook.router, prefix="/api/v1/notebook", tags=["notebook"], dependencies=_auth
)
app.include_router(memory.router, prefix="/api/v1/memory", tags=["memory"], dependencies=_auth)
app.include_router(personalization.router, prefix="/api/v1/memory", tags=["learner-model"], dependencies=_auth)
app.include_router(
    capabilities_settings.router,
    prefix="/api/v1/capabilities",
    tags=["capabilities"],
    dependencies=_auth,
)
app.include_router(
    sessions.router, prefix="/api/v1/sessions", tags=["sessions"], dependencies=_auth
)
app.include_router(
    question_notebook.router,
    prefix="/api/v1/question-notebook",
    tags=["question-notebook"],
    dependencies=_auth,
)
app.include_router(
    settings.router, prefix="/api/v1/settings", tags=["settings"], dependencies=_auth
)
app.include_router(
    mcp_settings.router,
    prefix="/api/v1/settings/mcp",
    tags=["mcp-settings"],
    dependencies=_auth,
)
app.include_router(
    personas.router, prefix="/api/v1/personas", tags=["personas"], dependencies=_auth
)
app.include_router(tools_router.router, prefix="/api/v1/tools", tags=["tools"], dependencies=_auth)
app.include_router(system.router, prefix="/api/v1/system", tags=["system"], dependencies=_auth)
app.include_router(
    traittutor_generate.router,
    prefix="/api/v1/traittutor/generate",
    tags=["traittutor-generate"],
    dependencies=_auth,
)
app.include_router(
    traittutor_profile.router,
    prefix="/api/v1/traittutor/profile",
    tags=["traittutor-profile"],
    dependencies=_auth,
)
app.include_router(voice.router, prefix="/api/v1/voice", tags=["voice"], dependencies=_auth)
app.include_router(
    attachments.router,
    prefix="/api/attachments",
    tags=["attachments"],
    dependencies=_auth,
)
app.include_router(outputs.router, prefix="/api/outputs", tags=["outputs"], dependencies=_auth)

# Unified WebSocket endpoint — auth is checked inside the handler (WebSockets
# cannot use FastAPI dependencies in the standard way)
app.include_router(unified_ws.router, prefix="/api/v1", tags=["unified-ws"])

# Quiz AI-judge WebSocket — same caveat as unified_ws above; auth is checked
# inside the handler so the WS upgrade isn't rejected by an HTTP-style dep.
app.include_router(quiz_judge.router, prefix="/api/v1", tags=["quiz-judge"])


@app.get("/")
async def root():
    return {"message": "Welcome to TraitTutor API"}


if __name__ == "__main__":
    from traittutor.api.run_server import main as run_server_main

    run_server_main()
