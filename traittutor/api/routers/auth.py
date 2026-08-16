"""Auth router — login, logout, status, registration, profile, and user-management endpoints."""

from contextvars import Token as _CtxToken
import hmac
import logging
import re
from typing import cast

from fastapi import (
    APIRouter,
    Cookie,
    Depends,
    File,
    Header,
    HTTPException,
    Response,
    UploadFile,
    WebSocket,
    status,
)
from fastapi.responses import FileResponse
from pydantic import AliasChoices, BaseModel, Field, field_validator

from traittutor.services.config import load_auth_settings, load_system_settings
from traittutor.services.config.origins import browser_origins_from_settings

# SameSite=None lets the cookie work when the browser accesses the frontend via
# 127.0.0.1 and the backend via localhost (different origins on the same machine).
# Browsers require Secure=True for SameSite=None, but that needs HTTPS — so in
# local dev we fall back to SameSite=Lax and tell users to use localhost:// URLs.
_SECURE = bool(load_auth_settings()["cookie_secure"])
_SAMESITE = "none" if _SECURE else "lax"

from traittutor.multi_user.context import set_current_user, user_from_token_payload
from traittutor.multi_user.models import CurrentUser
from traittutor.multi_user.paths import local_admin_user
from traittutor.services.auth import (
    AUTH_ENABLED,
    INITIAL_ADMIN_BOOTSTRAP_TOKEN,
    INITIAL_ADMIN_PASSWORD,
    INITIAL_ADMIN_USERNAME,
    TOKEN_EXPIRE_HOURS,
    TokenPayload,
    add_user,
    authenticate,
    change_password,
    create_initial_admin,
    create_token,
    decode_token,
    delete_user,
    get_user_info,
    list_users,
    set_avatar,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_COOKIE_NAME = "dt_token"
_COOKIE_MAX_AGE = TOKEN_EXPIRE_HOURS * 3600


def _cookie_attrs() -> dict:
    """Attribute set shared by ``login``'s ``set_cookie`` and ``logout``'s
    ``delete_cookie``.

    The deletion ``Set-Cookie`` must carry the same attributes as the one
    that created the cookie — ``delete_cookie`` defaults ``secure=False``,
    which browsers reject when paired with ``SameSite=None``, silently
    keeping the old cookie. See #623. Reads the module globals at call time
    so tests can monkeypatch ``_SECURE``/``_SAMESITE``.
    """
    return {
        "key": _COOKIE_NAME,
        "httponly": True,
        "samesite": _SAMESITE,
        "secure": _SECURE,
    }


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    """Payload for the POST /login endpoint."""

    username: str = Field(validation_alias=AliasChoices("email", "username"))
    password: str

    @field_validator("username")
    @classmethod
    def login_identity_valid(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Email or username cannot be empty")
        return value.casefold() if "@" in value else value


class RegisterRequest(BaseModel):
    """Payload for the POST /register endpoint."""

    username: str = Field(validation_alias=AliasChoices("email", "username"))
    password: str

    @field_validator("username")
    @classmethod
    def username_valid(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Email or username cannot be empty")
        email_re = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
        if "@" in v:
            if not email_re.match(v):
                raise ValueError("Enter a valid email address")
            return v.casefold()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{2,63}", v):
            raise ValueError(
                "Username must use 3-64 letters, numbers, dots, underscores, or hyphens"
            )
        return v

    @field_validator("password")
    @classmethod
    def password_valid(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


class BootstrapRequest(RegisterRequest):
    """First-admin creation, authorized only by deployment configuration."""

    token: str = ""


class AuthStatusResponse(BaseModel):
    """Response body for the GET /status endpoint."""

    enabled: bool
    authenticated: bool
    user_id: str | None = None
    username: str | None = None
    avatar: str = ""


class UserInfo(BaseModel):
    """Current learner account returned by the profile endpoint."""

    id: str = ""
    username: str
    created_at: str
    disabled: bool = False
    avatar: str = ""


# Markers settable through PUT /profile. Image markers ("img:<version>") are
# managed exclusively by the upload endpoint so users cannot point their
# avatar at a file that was never validated.
_ICON_MARKER_RE = re.compile(r"^icon:[a-z0-9-]{1,32}:[a-z0-9-]{1,32}$")

# User ids are generated as "u_<uuid hex>" (plus the "local-admin" /
# "env-admin" sentinels); reject anything else before it reaches the
# filesystem layer.
_USER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class UpdateProfileRequest(BaseModel):
    """Payload for the PUT /profile endpoint."""

    avatar: str

    @field_validator("avatar")
    @classmethod
    def avatar_valid(cls, v: str) -> str:
        v = v.strip()
        if v and not _ICON_MARKER_RE.match(v):
            raise ValueError("Avatar must be empty or 'icon:<name>:<color>'")
        return v


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def password_valid(cls, value: str) -> str:
        if len(value) < 8:
            raise ValueError("Password must be at least 8 characters")
        return value


class DeleteAccountRequest(BaseModel):
    current_password: str
    confirmation: str


# ---------------------------------------------------------------------------
# Shared helper — extract token from cookie or Bearer header
# ---------------------------------------------------------------------------


def _bearer_token_from_header(authorization: str | None) -> str | None:
    """Parse ``Authorization: Bearer <token>`` without using ``HTTPBearer``.

    ``HTTPBearer`` is a class-based dependency whose ``__call__`` is annotated
    ``request: Request``. FastAPI doesn't inject a Request into WebSocket
    dependency resolution, which makes ``HTTPBearer`` raise ``TypeError`` the
    moment a router with this dep mounts a WS endpoint. Doing the parse by
    hand keeps ``require_auth`` HTTP/WS-symmetric.
    """
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        token = parts[1].strip()
        return token or None
    return None


def _extract_token(authorization: str | None, dt_token: str | None) -> str | None:
    return _bearer_token_from_header(authorization) or dt_token


# ---------------------------------------------------------------------------
# Dependencies — reusable auth guards for other routers
# ---------------------------------------------------------------------------


def _install_current_user(payload: TokenPayload | None) -> _CtxToken:
    """Install the request-local current-user ContextVar from an auth result.

    Single point of truth for ``payload → CurrentUser`` so HTTP and WebSocket
    entry points produce identical user objects. ``payload is None`` means
    "no JWT was required" (AUTH_ENABLED=false) and resolves to the local
    admin user; a non-None payload resolves through ``user_from_token_payload``.

    Returns the ContextVar reset token. HTTP callers ignore it (the request
    ends with the task, so the var is GC'd with the task context). WebSocket
    callers keep it and call ``reset_current_user`` in their ``finally`` block,
    because a WS connection outlives the dependency-resolution task.

    ⚠ Invariant: every authenticated entry point MUST call this before the
    handler runs. Skipping it leaves ``get_current_path_service()`` falling
    back to the admin workspace — the silent-routing root cause of #481.
    """
    user = local_admin_user() if payload is None else user_from_token_payload(payload)
    return set_current_user(user)


async def require_auth(
    authorization: str | None = Header(default=None, alias="Authorization"),
    dt_token: str | None = Cookie(default=None),
) -> TokenPayload | None:
    """
    FastAPI dependency that enforces authentication when AUTH_ENABLED=true.

    Accepts the JWT from either:
      - Authorization: Bearer <token> header
      - dt_token cookie

    ``Header`` and ``Cookie`` are kept here in place of ``HTTPBearer`` so the
    function stays usable from WebSocket call sites that don't go through
    FastAPI's standard HTTP request lifecycle.

    Returns the authenticated TokenPayload, or None if auth is disabled.
    Raises HTTP 401 if auth is enabled but the token is missing or invalid.

    Declared ``async def`` so the ``set_current_user`` call runs in the same
    asyncio context as the endpoint. A sync dependency is dispatched via
    ``anyio.to_thread.run_sync``, which executes the function in a worker
    thread under a *copy* of the request context; any ``ContextVar.set``
    inside that thread is discarded when the thread returns, leaving the
    endpoint to read the unset default. That regression was the root cause
    of #481.
    """
    if not AUTH_ENABLED:
        _install_current_user(None)
        return None

    token = _extract_token(authorization, dt_token)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    _install_current_user(payload)
    return payload


class _WsAuthFailed:
    """Sentinel: ws_require_auth failed and closed the WebSocket."""


ws_auth_failed: _WsAuthFailed = _WsAuthFailed()


def _websocket_origin_allowed(ws: WebSocket, *, query_token: str | None) -> bool:
    """Enforce the HTTP CORS allowlist for browser-authenticated sockets.

    Browsers always send ``Origin`` and cannot suppress or forge it. Originless
    query-token clients are retained for the existing non-browser API contract;
    cookie authentication never gets that exception.
    """

    raw_origin = ws.headers.get("origin")
    if not raw_origin:
        return bool(query_token)
    origin = raw_origin.strip()
    if origin in {"", "*", "null"}:
        return False
    return origin in browser_origins_from_settings(load_system_settings())


async def ws_require_auth(ws: WebSocket) -> _CtxToken | _WsAuthFailed:
    """Authenticate a WebSocket connection and set the user ContextVar.

    Must be called **before** ``ws.accept()`` so the server can reject
    unauthenticated upgrades cleanly.

    Returns a ContextVar reset token on success, or ``ws_auth_failed``
    on failure (the WebSocket is already closed — the caller should
    ``return`` immediately).

    Usage::

        user_token = await ws_require_auth(ws)
        if user_token is ws_auth_failed:
            return
        await ws.accept()
        try:
            ...
        finally:
            reset_current_user(user_token)
    """
    if not AUTH_ENABLED:
        return _install_current_user(None)

    query_token = ws.query_params.get("token")
    if not _websocket_origin_allowed(ws, query_token=query_token):
        await ws.close(code=4003)
        return ws_auth_failed

    token = query_token or ws.cookies.get(_COOKIE_NAME)
    payload = decode_token(token) if token else None
    if not payload:
        await ws.close(code=4001)
        return ws_auth_failed

    return _install_current_user(payload)


def ws_reset_auth(token: _CtxToken | _WsAuthFailed) -> None:
    """Reset a successful WebSocket auth context without leaking the sentinel type."""
    if isinstance(token, _WsAuthFailed):
        return
    from traittutor.multi_user.context import reset_current_user

    reset_current_user(cast(_CtxToken[CurrentUser | None], token))


async def require_admin(
    payload: TokenPayload | None = Depends(require_auth),
) -> TokenPayload:
    """
    FastAPI dependency that requires the caller to be an admin.

    Raises HTTP 403 if the authenticated user is not an admin.
    When AUTH_ENABLED=false, all requests are treated as admin.

    ``async def`` mirrors ``require_auth`` so the dependency chain stays on
    the event loop and the user ContextVar set by ``require_auth`` is visible
    to the endpoint.
    """
    if not AUTH_ENABLED:
        return _local_admin_token_payload()

    if payload is None or payload.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return payload


def _local_admin_token_payload() -> TokenPayload:
    """Synthetic admin payload used when AUTH_ENABLED=false.

    Mirrors the local admin identity (LOCAL_ADMIN_USERNAME / LOCAL_ADMIN_ID)
    so audit logs and self-reference checks behave the same as in multi-user
    mode. Values are kept aligned with ``local_admin_user()`` in
    ``traittutor/multi_user/paths.py``.
    """
    from traittutor.multi_user.models import LOCAL_ADMIN_ID, LOCAL_ADMIN_USERNAME

    return TokenPayload(
        username=LOCAL_ADMIN_USERNAME,
        role="admin",
        user_id=LOCAL_ADMIN_ID,
    )


# ---------------------------------------------------------------------------
# Public endpoints (no auth required)
# ---------------------------------------------------------------------------


@router.get("/status", response_model=AuthStatusResponse)
async def auth_status(
    authorization: str | None = Header(default=None, alias="Authorization"),
    dt_token: str | None = Cookie(default=None),
) -> AuthStatusResponse:
    """Return whether auth is enabled and whether the current request is authenticated."""
    if not AUTH_ENABLED:
        return AuthStatusResponse(
            enabled=False,
            authenticated=True,
            user_id="local-admin",
            username="local",
        )

    token = _extract_token(authorization, dt_token)
    payload = decode_token(token) if token else None
    avatar = ""
    if payload is not None:
        info = get_user_info(payload.username)
        if info:
            avatar = str(info.get("avatar") or "")
    return AuthStatusResponse(
        enabled=True,
        authenticated=payload is not None,
        user_id=payload.user_id if payload else None,
        username=payload.username if payload else None,
        avatar=avatar,
    )


@router.post("/login")
async def login(body: LoginRequest, response: Response) -> dict:
    """Validate credentials and set a JWT cookie."""
    if not AUTH_ENABLED:
        return {"ok": True, "message": "Auth is disabled — no login required."}

    result = authenticate(body.username, body.password)
    if not result:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )

    token = create_token(result.username, result.role, result.user_id)
    response.set_cookie(value=token, max_age=_COOKIE_MAX_AGE, **_cookie_attrs())

    logger.info(f"User '{result.username}' logged in (role={result.role!r})")
    return {
        "ok": True,
        "user_id": result.user_id,
        "username": result.username,
    }


@router.post("/logout")
async def logout(response: Response) -> dict:
    """Clear the JWT cookie.

    Deletion attributes mirror ``login`` structurally via ``_cookie_attrs()``
    (see the rationale there and #623).
    """
    response.delete_cookie(**_cookie_attrs())
    return {"ok": True}


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest) -> dict:
    """
    Public self-service registration for learner accounts.

    Only available when AUTH_ENABLED=true and the
    ``allow_public_registration`` runtime setting is enabled (disabled by
    default). Accounts created here always get the "user" role — the first
    administrator is created exclusively through POST /bootstrap.
    """
    if not AUTH_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Auth is disabled — registration is not available.",
        )

    auth_settings = load_auth_settings()
    if not bool(auth_settings.get("allow_public_registration", False)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Public registration is disabled"
        )

    existing = {u["username"] for u in list_users()}
    if body.username in existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username already taken",
        )

    add_user(body.username, body.password)
    user_id = ""
    for item in list_users():
        if item.get("username") == body.username:
            user_id = str(item.get("id") or "")
            break
    logger.info(f"User registered: '{body.username}'")
    return {
        "ok": True,
        "user_id": user_id,
        "username": body.username,
    }


@router.get("/bootstrap")
async def bootstrap_status() -> dict:
    """Expose setup state without revealing deployment credentials."""
    configured = bool(INITIAL_ADMIN_USERNAME and INITIAL_ADMIN_PASSWORD) or bool(
        INITIAL_ADMIN_BOOTSTRAP_TOKEN
    )
    return {"initialized": bool(list_users()), "bootstrap_configured": configured}


@router.post("/bootstrap", status_code=status.HTTP_201_CREATED)
async def bootstrap_admin(body: BootstrapRequest) -> dict:
    """Create the first administrator from an explicit deployment secret."""
    if not AUTH_ENABLED:
        raise HTTPException(status_code=400, detail="Auth is disabled")
    credentials_match = (
        bool(INITIAL_ADMIN_USERNAME and INITIAL_ADMIN_PASSWORD)
        and hmac.compare_digest(body.username, INITIAL_ADMIN_USERNAME)
        and hmac.compare_digest(body.password, INITIAL_ADMIN_PASSWORD)
    )
    token_match = bool(INITIAL_ADMIN_BOOTSTRAP_TOKEN) and hmac.compare_digest(
        body.token, INITIAL_ADMIN_BOOTSTRAP_TOKEN
    )
    if not (credentials_match or token_match):
        raise HTTPException(status_code=403, detail="Bootstrap authorization failed")
    record = create_initial_admin(body.username, body.password)
    if record is None:
        raise HTTPException(status_code=409, detail="Administrator bootstrap is already complete")
    return {"ok": True, "user_id": str(record.get("id") or ""), "username": body.username}


# ---------------------------------------------------------------------------
# Profile endpoints (any authenticated user, self-service)
# ---------------------------------------------------------------------------

_AVATAR_MAX_BYTES = 1 * 1024 * 1024
_AVATAR_MEDIA_TYPES = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}


def _sniff_image(data: bytes) -> str | None:
    """Detect a supported raster image format from its magic bytes.

    The uploaded filename and Content-Type are attacker-controlled, so the
    stored extension (and the media type served back) is derived from the
    bytes alone. SVG is deliberately unsupported — serving user-supplied SVG
    is a stored-XSS vector.
    """
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def _require_profile_identity(payload: TokenPayload | None) -> TokenPayload:
    """Shared guard for the self-service profile endpoints."""
    if not AUTH_ENABLED or payload is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Auth is disabled — profiles are not available.",
        )
    return payload


@router.get("/profile", response_model=UserInfo)
async def get_profile(
    payload: TokenPayload | None = Depends(require_auth),
) -> UserInfo:
    """Return the current user's own account info."""
    current = _require_profile_identity(payload)
    info = get_user_info(current.username)
    if info is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return UserInfo(**info)


@router.put("/account/password")
async def update_account_password(
    body: ChangePasswordRequest,
    payload: TokenPayload | None = Depends(require_auth),
) -> dict:
    current = _require_profile_identity(payload)
    if not change_password(current.username, body.current_password, body.new_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect"
        )
    logger.info("Password changed for '%s'", current.username)
    return {"ok": True}


@router.delete("/account")
async def delete_current_account(
    body: DeleteAccountRequest,
    response: Response,
    payload: TokenPayload | None = Depends(require_auth),
) -> dict:
    current = _require_profile_identity(payload)
    if body.confirmation != "DELETE":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Type DELETE to confirm"
        )
    if not authenticate(current.username, body.current_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect"
        )
    info = get_user_info(current.username) or {}
    if not delete_user(current.username):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")
    user_id = str(info.get("id") or current.user_id)
    if user_id and _USER_ID_RE.match(user_id):
        import shutil

        from traittutor.multi_user.identity import delete_avatar_file
        from traittutor.multi_user.paths import USERS_ROOT

        delete_avatar_file(user_id)
        workspace = (USERS_ROOT / user_id).resolve()
        if workspace.is_relative_to(USERS_ROOT.resolve()):
            shutil.rmtree(workspace, ignore_errors=True)
    response.delete_cookie(**_cookie_attrs())
    logger.info("Account deleted for '%s'", current.username)
    return {"ok": True}


@router.put("/profile")
async def update_profile(
    body: UpdateProfileRequest,
    payload: TokenPayload | None = Depends(require_auth),
) -> dict:
    """Update the current user's own avatar marker (icon choice or reset).

    Only the validated ``icon:<name>:<color>`` form (or empty string) is
    accepted here; ``img:`` markers are owned by the upload endpoint.
    """
    current = _require_profile_identity(payload)
    if not set_avatar(current.username, body.avatar):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    # The marker no longer references an uploaded image, so drop the file.
    from traittutor.multi_user.identity import delete_avatar_file

    if current.user_id and _USER_ID_RE.match(current.user_id):
        delete_avatar_file(current.user_id)
    return {"ok": True, "avatar": body.avatar}


@router.put("/profile/avatar")
async def upload_avatar(
    file: UploadFile = File(...),
    payload: TokenPayload | None = Depends(require_auth),
) -> dict:
    """Upload an avatar image for the current user.

    The client is expected to crop/resize before uploading; the server only
    enforces a size cap and validates the format by magic bytes.
    """
    current = _require_profile_identity(payload)
    if not current.user_id or not _USER_ID_RE.match(current.user_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot store an avatar for this account.",
        )
    info = get_user_info(current.username)
    if info is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    data = await file.read(_AVATAR_MAX_BYTES + 1)
    if len(data) > _AVATAR_MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Avatar image is too large (max 1 MB).",
        )
    ext = _sniff_image(data)
    if ext is None:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Avatar must be a PNG, JPEG or WebP image.",
        )

    from traittutor.multi_user.identity import save_avatar_file

    # Bump the version embedded in the marker so clients cache-bust the URL.
    previous = str(info.get("avatar") or "")
    version = 1
    if previous.startswith("img:"):
        try:
            version = int(previous.split(":", 1)[1]) + 1
        except ValueError:
            version = 1
    marker = f"img:{version}"

    save_avatar_file(current.user_id, data, ext)
    if not set_avatar(current.username, marker):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    logger.info(f"User '{current.username}' uploaded a new avatar ({ext}, {len(data)} bytes)")
    return {"ok": True, "avatar": marker}


@router.delete("/profile/avatar")
async def remove_avatar(
    payload: TokenPayload | None = Depends(require_auth),
) -> dict:
    """Remove the current user's uploaded avatar image and reset the marker."""
    current = _require_profile_identity(payload)
    from traittutor.multi_user.identity import delete_avatar_file

    if current.user_id and _USER_ID_RE.match(current.user_id):
        delete_avatar_file(current.user_id)
    set_avatar(current.username, "")
    return {"ok": True, "avatar": ""}


@router.get("/avatar/{user_id}")
async def get_avatar_image(
    user_id: str,
    _: TokenPayload | None = Depends(require_auth),
) -> FileResponse:
    """Serve a stored avatar image. Any authenticated user may view avatars
    (they appear in the admin table and next to the viewer's own profile)."""
    if not _USER_ID_RE.match(user_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Avatar not found")

    from traittutor.multi_user.identity import get_avatar_file

    target = get_avatar_file(user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Avatar not found")

    media_type = _AVATAR_MEDIA_TYPES.get(target.suffix.lstrip("."), "application/octet-stream")
    headers = {
        # Private user content; the marker version in the URL handles busting.
        "Cache-Control": "private, max-age=86400",
        "X-Content-Type-Options": "nosniff",
        "Content-Disposition": "inline",
    }
    return FileResponse(path=str(target), media_type=media_type, headers=headers)
