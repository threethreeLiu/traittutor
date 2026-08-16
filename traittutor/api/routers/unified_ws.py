"""Canonical WebSocket endpoint for starting turns and structured Ask User replies."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from traittutor.core.stream import StreamEvent, StreamEventType

router = APIRouter()
logger = logging.getLogger(__name__)


@router.websocket("/ws")
async def unified_websocket(ws: WebSocket) -> None:
    from traittutor.api.routers.auth import ws_auth_failed, ws_require_auth, ws_reset_auth
    from traittutor.services.session import get_turn_runtime_manager

    user_token = await ws_require_auth(ws)
    if user_token is ws_auth_failed:
        return
    await ws.accept()
    closed = False
    forwarding: asyncio.Task[None] | None = None
    active_runtime: Any | None = None
    active_turn_id = ""

    async def safe_send(data: dict[str, Any]) -> None:
        nonlocal closed
        if closed:
            return
        try:
            await ws.send_text(json.dumps(data, ensure_ascii=False, default=str))
        except Exception:
            closed = True

    async def send_error(message: str, *, turn_id: str = "") -> None:
        await safe_send(
            StreamEvent(
                type=StreamEventType.ERROR,
                source="unified_ws",
                content=message,
                turn_id=turn_id,
                metadata={"turn_terminal": True, "status": "rejected"},
            ).to_dict()
        )

    async def stop_forwarding() -> None:
        nonlocal forwarding
        if forwarding is None:
            return
        forwarding.cancel()
        try:
            await forwarding
        except asyncio.CancelledError:
            pass
        forwarding = None

    async def forward_turn(runtime: Any, turn_id: str) -> None:
        nonlocal forwarding
        await stop_forwarding()

        async def _forward() -> None:
            async for event in runtime.subscribe_turn(turn_id, after_seq=0):
                await safe_send(event)

        forwarding = asyncio.create_task(_forward())

    try:
        while not closed:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await send_error("Invalid JSON.")
                continue
            if not isinstance(msg, dict):
                await send_error("WebSocket message must be an object.")
                continue

            runtime = get_turn_runtime_manager()
            msg_type = msg.get("type")
            if msg_type == "start_turn":
                try:
                    _, turn = await runtime.start_turn(msg)
                except RuntimeError as exc:
                    await send_error(str(exc))
                    continue
                active_runtime = runtime
                active_turn_id = str(turn["id"])
                await forward_turn(runtime, active_turn_id)
                continue

            if msg_type == "submit_user_reply":
                turn_id = str(msg.get("turn_id") or "").strip()
                answers_raw = msg.get("answers")
                if not turn_id or not isinstance(answers_raw, list) or not answers_raw:
                    await send_error("submit_user_reply requires turn_id and answers.")
                    continue
                answers: list[dict[str, str]] = []
                for entry in answers_raw:
                    if not isinstance(entry, dict):
                        answers = []
                        break
                    question_id = str(entry.get("questionId") or "").strip()
                    if not question_id or not isinstance(entry.get("text"), str):
                        answers = []
                        break
                    answers.append({"questionId": question_id, "text": entry["text"]})
                if not answers:
                    await send_error("submit_user_reply answers are invalid.", turn_id=turn_id)
                    continue
                if await runtime.get_owned_turn(turn_id) is None:
                    await send_error("Turn not found or unavailable.", turn_id=turn_id)
                    continue
                if not await runtime.submit_user_reply(turn_id, answers=answers):
                    await send_error(
                        f"Turn {turn_id} is not awaiting a user reply.", turn_id=turn_id
                    )
                continue

            await send_error(f"Unknown type: {msg_type}")
    except WebSocketDisconnect:
        logger.debug("Client disconnected from /ws")
    except Exception as exc:
        logger.error("Unified WS error: %s", exc, exc_info=True)
        await send_error(str(exc))
    finally:
        closed = True
        # Closing the connection is the canonical cancellation signal.  This
        # preserves the two-message WS protocol while still stopping the
        # owner-bound Gateway execution instead of merely abandoning its
        # event subscriber.
        if active_runtime is not None and active_turn_id:
            await active_runtime.cancel_turn(active_turn_id)
        await stop_forwarding()
        if user_token is not None:
            ws_reset_auth(user_token)
