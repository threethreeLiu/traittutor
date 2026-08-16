"""Idempotent projection of a Search receipt into the canonical chat thread."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from traittutor.services.session.protocol import SessionStoreProtocol

from .models import CapabilityDecision, SearchReceipt


@dataclass(frozen=True)
class SearchThreadDelivery:
    """Opaque IDs proving that both sides of the Search exchange were stored."""

    session_id: str
    user_message_id: int
    message_id: int


def _decision_id(message: dict[str, Any]) -> str:
    metadata = message.get("metadata")
    if not isinstance(metadata, dict):
        return ""
    value = metadata.get("assistant_route_decision_id")
    return value if isinstance(value, str) else ""


def _message_id(message: dict[str, Any]) -> int | None:
    value = message.get("id")
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and value.isdigit() and int(value) > 0:
        return int(value)
    return None


def render_search_thread_answer(receipt: SearchReceipt) -> str:
    """Render only structured tool citations as the answer's source list."""
    lines = [receipt.content.strip()]
    if receipt.sources:
        lines.extend(("", "Sources"))
        for source in receipt.sources:
            title = " ".join(source.title.split())
            safe_url = source.url.replace("<", "%3C").replace(">", "%3E")
            lines.append(f"- {source.reference} {title}: <{safe_url}>")
    return "\n".join(lines).strip()


async def deliver_search_to_thread(
    *,
    store: SessionStoreProtocol,
    decision: CapabilityDecision,
    query: str,
) -> SearchThreadDelivery:
    """Write one user/Search pair and reuse it on route replay.

    The decision ID is server-minted and stored in message metadata. It is the
    idempotency join between the routing ledger and the existing session store;
    browser-provided source objects are never accepted here.
    """
    receipt = decision.search_receipt
    if decision.capability != "search" or receipt is None:
        raise ValueError("search decision has no receipt")

    if decision.session_id:
        session = await store.get_session(decision.session_id)
        if session is None:
            raise ValueError("Search session not found")
    else:
        session = await store.ensure_session()
    session_id = str(session.get("session_id") or session.get("id") or "").strip()
    if not session_id:
        raise RuntimeError("session store returned no session ID")

    messages = await store.get_messages(session_id)
    routed_messages = [
        message for message in messages if _decision_id(message) == decision.decision_id
    ]
    user_message_id = next(
        (
            identifier
            for message in routed_messages
            if message.get("role") == "user"
            for identifier in [_message_id(message)]
            if identifier is not None
        ),
        None,
    )
    assistant_message_id = next(
        (
            identifier
            for message in routed_messages
            if message.get("role") == "assistant"
            for identifier in [_message_id(message)]
            if identifier is not None
        ),
        None,
    )
    if user_message_id is not None and assistant_message_id is not None:
        return SearchThreadDelivery(session_id, user_message_id, assistant_message_id)

    common_metadata = {
        "assistant_route_decision_id": decision.decision_id,
        "assistant_route_contract": "search-receipt.v1",
    }
    if user_message_id is None:
        user_message_id = await store.add_message(
            session_id,
            role="user",
            content=query,
            capability="search",
            metadata={**common_metadata, "server_authored": False},
        )
        if user_message_id <= 0:
            raise RuntimeError("session store did not persist the Search query")

    if assistant_message_id is None:
        assistant_message_id = await store.add_message(
            session_id,
            role="assistant",
            content=render_search_thread_answer(receipt),
            capability="search",
            metadata={
                **common_metadata,
                "server_authored": True,
                "source_refs": list(receipt.source_refs),
                "sources": [source.model_dump(mode="json") for source in receipt.sources],
                "degradation_code": receipt.degradation_code,
            },
            parent_message_id=user_message_id,
        )
        if assistant_message_id <= 0:
            raise RuntimeError("session store did not persist the Search answer")

    return SearchThreadDelivery(session_id, user_message_id, assistant_message_id)


__all__ = ["SearchThreadDelivery", "deliver_search_to_thread", "render_search_thread_answer"]
