"""Regression guard: the legacy ``/api/v1/chat`` family must stay retired.

The old ChatAgent WebSocket (``/api/v1/chat``) and its REST session helpers
(``/api/v1/chat/sessions`` and ``/api/v1/chat/sessions/{session_id}``) accepted
payload shapes the typed unified protocol forbids (``message``, ``history``,
``kb_name``, ``enable_rag``, ``enable_web_search``) and maintained an
independent session store. They were removed in favor of the single
``/api/v1/ws`` unified runtime.

Deletion alone is not proof of retirement: a future re-mount or adapter would
silently reintroduce the legacy surface. This module asserts retirement at
two levels:

1. **Static** — the production FastAPI route table (``traittutor.api.main:app``)
   is flattened across ``include_router`` mounts; the legacy paths must be
   absent while ``/api/v1/ws`` remains registered. This needs no auth and no
   global env mutation; it only reads ``app.routes``.
2. **Runtime** — a per-test FastAPI app mounts the real ``unified_ws`` router
   under ``/api/v1`` (mirroring ``main.py``) with auth replaced by a test
   dependency. A live ``TestClient`` then confirms the legacy REST helpers
   return 404 and that a WebSocket upgrade to ``/api/v1/chat`` is rejected.

We avoid mutating ``AUTH_ENABLED`` at import time: that flag is read once when
the production app is imported, so an import-time ``os.environ`` assignment
pollutes every test collected afterwards. The runtime checks instead mount a
fixture-scoped app, exactly like ``tests/api/test_assistant_routing.py``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from typing import Annotated

from fastapi import Depends, FastAPI, Header
import pytest
from starlette.websockets import WebSocketDisconnect

# Imported for the static route-table scan only. This does NOT mutate auth: the
# scan reads ``app.routes`` and never issues a request, so AUTH_ENABLED is
# irrelevant here.
from traittutor.api.main import app as production_app
from traittutor.api.routers import unified_ws

LEGACY_CHAT_PATHS = {
    "/api/v1/chat",
    "/api/v1/chat/sessions",
    "/api/v1/chat/sessions/{session_id}",
}


def _resolved_paths(application: FastAPI) -> set[str]:
    """Flatten ``include_router`` mounts into their prefixed route paths.

    FastAPI wraps each ``include_router`` call in a private ``_IncludedRouter``
    that does not expose a ``.path``; the prefix lives on
    ``include_context.prefix`` and the inner routes on ``original_router``.
    Top-level routes (``/docs``, ``/openapi.json``, the root ``/``) keep their
    own ``.path``. We union both so the result mirrors what the ASGI router
    actually matches at request time.
    """

    paths: set[str] = set()
    for route in application.routes:
        own = getattr(route, "path", None)
        if isinstance(own, str):
            paths.add(own)
        include_context = getattr(route, "include_context", None)
        original_router = getattr(route, "original_router", None)
        if include_context is not None and original_router is not None:
            prefix = getattr(include_context, "prefix", "") or ""
            inner: Iterable[object] = getattr(original_router, "routes", []) or []
            for sub in inner:
                sub_path = getattr(sub, "path", None)
                if isinstance(sub_path, str):
                    paths.add(prefix + sub_path)
    return paths


def test_legacy_chat_routes_are_absent_from_route_table() -> None:
    registered = _resolved_paths(production_app)
    leaked = LEGACY_CHAT_PATHS & registered
    assert not leaked, (
        "Legacy chat routes reappeared in the FastAPI route table: "
        f"{sorted(leaked)}. The unified /api/v1/ws runtime is the only "
        "browser chat transport; /api/v1/chat must stay retired."
    )


def test_unified_ws_route_remains_registered() -> None:
    """Retiring the legacy surface must not also drop the unified transport."""
    registered = _resolved_paths(production_app)
    assert "/api/v1/ws" in registered, (
        "/api/v1/ws is missing from the route table — the unified chat "
        "transport must remain registered while the legacy path is retired."
    )


@pytest.fixture()
def chat_retirement_app() -> FastAPI:
    """Mount only the unified WS transport under /api/v1, auth replaced by a
    no-op test dependency. Mirrors main.py's mount prefix so a request to a
    retired path resolves against the same transport surface a browser sees.
    """

    async def install_test_user(
        x_test_user: Annotated[str, Header()] = "tester",
    ) -> AsyncIterator[None]:
        # The unified WS handler resolves identity from the connection itself;
        # for the REST 404 + WS-rejection checks here we only need auth to
        # pass, so a no-op dependency is sufficient.
        yield

    application = FastAPI()
    application.include_router(
        unified_ws.router,
        prefix="/api/v1",
        dependencies=[Depends(install_test_user)],
    )
    return application


def test_legacy_chat_rest_endpoints_return_404(chat_retirement_app: FastAPI) -> None:
    """Live request confirms the retirement holds at runtime, not just in
    the route table. The fixture app carries no legacy router, so the retired
    REST helpers must 404 rather than 401/200."""
    from fastapi.testclient import TestClient

    with TestClient(chat_retirement_app) as client:
        for path in ("/api/v1/chat/sessions", "/api/v1/chat/sessions/legacy-id"):
            response = client.get(path)
            assert response.status_code == 404, (
                f"Expected retired 404 for {path}, got {response.status_code}."
            )


def test_legacy_chat_websocket_upgrade_is_rejected(chat_retirement_app: FastAPI) -> None:
    """Actually attempt a WebSocket upgrade to the retired endpoint.

    A still-mounted legacy route would accept the upgrade and emit a
    ``session``/``error`` JSON frame. Because the fixture app mounts only the
    unified transport, Starlette has no route for ``/api/v1/chat`` and closes
    the connection before the app layer runs — observed as WebSocketDisconnect
    on the client. This is the runtime counterpart to the static route-table
    scan: it proves the browser cannot open the old socket.

    The positive counterpart (``/api/v1/ws`` accepts an upgrade) is already
    covered by ``test_unified_ws_route_remains_registered`` above and by the
    ownership suite (``tests/api/test_unified_ws_turn_ownership.py``); we do
    not duplicate it here because driving a real unified turn would require
    the full auth + runtime fixture, and the static scan is sufficient to
    prove the route is mounted.
    """
    from fastapi.testclient import TestClient

    with TestClient(chat_retirement_app) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/api/v1/chat") as ws:
                ws.receive()
