"""Canonical Gateway coverage for the browser-facing notebook summary stream."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from traittutor.agents.notebook.summarize_agent import (
    NotebookSummarizeAgent,
    NotebookSummaryCancelled,
)
from traittutor.api.routers import notebook as notebook_router
from traittutor.multi_user.context import reset_current_user, set_current_user
from traittutor.multi_user.models import CurrentUser, UserScope


def _summary_kwargs() -> dict[str, Any]:
    return {
        "title": "Limit proof",
        "record_type": "question",
        "user_query": "What is epsilon-delta?",
        "output": "It defines a bound for closeness.",
        "metadata": {"ui_language": "en"},
    }


@pytest.mark.asyncio
async def test_notebook_summary_gateway_projects_text_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reasoning, tools, usage, and receipts must not enter a saved summary."""
    received: list[Any] = []

    class Gateway:
        async def stream(self, request: Any):
            received.append(request)
            yield SimpleNamespace(type="reasoning", text="private chain")
            yield SimpleNamespace(type="text", text="Safe ")
            yield SimpleNamespace(type="tool_call", text="tool args")
            yield SimpleNamespace(type="usage", text="token counts")
            yield SimpleNamespace(type="text", text="summary")
            yield SimpleNamespace(type="final", text=None, receipt=object())

    monkeypatch.setattr("traittutor.gateway.get_gateway", lambda: Gateway())

    owner = CurrentUser(
        id="notebook-owner",
        username="notebook-owner",
        role="user",
        scope=UserScope(kind="user", user_id="notebook-owner", root=Path(__file__).parent),
    )
    context_token = set_current_user(owner)
    try:
        agent = NotebookSummarizeAgent(language="en")
        result = [chunk async for chunk in agent.stream_summary(**_summary_kwargs())]
    finally:
        reset_current_user(context_token)

    assert result == ["Safe ", "summary"]
    assert len(received) == 1
    request = received[0]
    assert request.purpose == "notebook:summary"
    assert request.timeout_seconds == 30.0
    assert request.temperature == 0.2
    assert request.max_tokens == 300
    assert request.user_id == "notebook-owner"
    assert request.tools == ()
    assert request.attachments == ()
    assert [(message.role, message.content) for message in request.messages] == [
        ("system", request.system_prompt),
        ("user", request.prompt),
    ]
    assert "private chain" not in "".join(result)
    assert "tool args" not in "".join(result)
    assert "token counts" not in "".join(result)


@pytest.mark.asyncio
async def test_notebook_summary_gateway_cancelled_stops_before_partial_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A typed cancellation ends this path without surfacing provider metadata."""

    class Gateway:
        async def stream(self, _request: Any):
            yield SimpleNamespace(type="cancelled", text=None, receipt=object())

    monkeypatch.setattr("traittutor.gateway.get_gateway", lambda: Gateway())

    agent = NotebookSummarizeAgent(language="en")
    with pytest.raises(NotebookSummaryCancelled):
        _ = [chunk async for chunk in agent.stream_summary(**_summary_kwargs())]


@pytest.mark.asyncio
async def test_notebook_summary_cancellation_never_reaches_record_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The SSE handler must not turn a cancelled Gateway stream into a record."""

    class CancelledAgent:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def stream_summary(self, **_kwargs: Any):
            raise NotebookSummaryCancelled("cancelled")
            yield "unreachable"

    class Manager:
        def add_record(self, **_kwargs: Any) -> dict[str, Any]:
            pytest.fail("cancelled summary must not be persisted")

    monkeypatch.setattr(notebook_router, "NotebookSummarizeAgent", CancelledAgent)
    monkeypatch.setattr(notebook_router, "notebook_manager", Manager())
    request = notebook_router.AddRecordRequest(
        notebook_ids=["notebook-1"],
        record_type="question",
        title="Limit proof",
        user_query="What is epsilon-delta?",
        output="It defines a bound for closeness.",
        metadata={"ui_language": "en"},
    )

    events = [event async for event in notebook_router._stream_add_record_with_summary(request)]

    assert events == []
