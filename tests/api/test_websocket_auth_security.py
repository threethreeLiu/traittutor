"""Security boundaries for cookie-authenticated WebSocket handshakes."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from traittutor.api.routers import auth


class _WebSocket:
    def __init__(
        self,
        *,
        origin: str | None,
        cookie_token: str | None = "victim-cookie",
        query_token: str | None = None,
    ) -> None:
        self.headers = {"origin": origin} if origin is not None else {}
        self.cookies = {"dt_token": cookie_token} if cookie_token else {}
        self.query_params = {"token": query_token} if query_token else {}
        self.closed_code: int | None = None

    async def close(self, *, code: int) -> None:
        self.closed_code = code


@pytest.fixture
def authenticated_ws(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth, "AUTH_ENABLED", True)
    monkeypatch.setattr(
        auth,
        "load_system_settings",
        lambda: {
            "frontend_port": 3000,
            "cors_origin": "https://app.example",
            "cors_origins": [],
        },
        raising=False,
    )
    monkeypatch.setattr(
        auth,
        "decode_token",
        lambda token: SimpleNamespace(token=token) if token else None,
    )
    monkeypatch.setattr(auth, "_install_current_user", lambda payload: ("installed", payload.token))


@pytest.mark.asyncio
async def test_cookie_websocket_rejects_hostile_origin_before_installing_user(
    authenticated_ws: None,
) -> None:
    websocket = _WebSocket(origin="https://attacker.example")

    result = await auth.ws_require_auth(websocket)  # type: ignore[arg-type]

    assert result is auth.ws_auth_failed
    assert websocket.closed_code == 4003


@pytest.mark.asyncio
async def test_cookie_websocket_requires_exact_origin_without_path(
    authenticated_ws: None,
) -> None:
    websocket = _WebSocket(origin="https://app.example/attacker-controlled")

    result = await auth.ws_require_auth(websocket)  # type: ignore[arg-type]

    assert result is auth.ws_auth_failed
    assert websocket.closed_code == 4003


@pytest.mark.asyncio
async def test_cookie_websocket_requires_origin(authenticated_ws: None) -> None:
    websocket = _WebSocket(origin=None)

    result = await auth.ws_require_auth(websocket)  # type: ignore[arg-type]

    assert result is auth.ws_auth_failed
    assert websocket.closed_code == 4003


@pytest.mark.asyncio
async def test_cookie_websocket_accepts_exact_configured_origin(authenticated_ws: None) -> None:
    websocket = _WebSocket(origin="https://app.example")

    result = await auth.ws_require_auth(websocket)  # type: ignore[arg-type]

    assert result == ("installed", "victim-cookie")
    assert websocket.closed_code is None


@pytest.mark.asyncio
async def test_non_browser_query_token_keeps_originless_compatibility(
    authenticated_ws: None,
) -> None:
    websocket = _WebSocket(origin=None, cookie_token=None, query_token="api-token")

    result = await auth.ws_require_auth(websocket)  # type: ignore[arg-type]

    assert result == ("installed", "api-token")
    assert websocket.closed_code is None
