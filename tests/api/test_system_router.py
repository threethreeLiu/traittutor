from __future__ import annotations

from types import SimpleNamespace

import pytest

from traittutor.api.routers import system as system_router


@pytest.mark.asyncio
async def test_embeddings_connection_uses_batch_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, list[str]] = {}

    class _FakeClient:
        async def embed(self, texts: list[str]):
            captured["texts"] = texts
            return [[0.1, 0.2], [0.3, 0.4]]

    monkeypatch.setattr(
        system_router,
        "get_embedding_config",
        lambda: SimpleNamespace(model="embed-test", binding="openai"),
    )
    monkeypatch.setattr(system_router, "get_embedding_client", lambda: _FakeClient())

    response = await system_router.test_embeddings_connection()

    assert response.success is True
    assert captured["texts"] == ["test", "retrieval batch probe"]


@pytest.mark.asyncio
async def test_embeddings_connection_rejects_partial_batch_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeClient:
        async def embed(self, texts: list[str]):
            return [[0.1, 0.2]]

    monkeypatch.setattr(
        system_router,
        "get_embedding_config",
        lambda: SimpleNamespace(model="embed-test", binding="openai"),
    )
    monkeypatch.setattr(system_router, "get_embedding_client", lambda: _FakeClient())

    response = await system_router.test_embeddings_connection()

    assert response.success is False
    assert response.message == "Embeddings connection failed: Invalid response"
