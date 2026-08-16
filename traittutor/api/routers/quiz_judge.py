"""AI judge WebSocket — grades a learner's quiz answer.

Mounted on its own (without router-level HTTP auth dependencies) because
WebSocket upgrades cannot use FastAPI's HTTP dependency injection, so we
rely on ``ws_require_auth`` inside the handler — mirroring the pattern
used by ``unified_ws``.
"""

from __future__ import annotations

import asyncio
import base64 as _b64
from collections.abc import AsyncIterator
import logging
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from traittutor.gateway import GatewayAttachment, GatewayMessage, GatewayRequest, get_gateway
from traittutor.services.config import PROJECT_ROOT, load_config_with_main
from traittutor.services.settings.interface_settings import get_ui_language
from traittutor.utils.error_utils import format_exception_message

logger = logging.getLogger(__name__)
_config = load_config_with_main("main.yaml", PROJECT_ROOT)

router = APIRouter()


_JUDGE_SYSTEM_PROMPTS = {
    "zh": (
        "你是一名严谨且鼓励学习者的助教，正在批改一道测验题。"
        "请基于题目、参考答案与解析，对学习者的作答给出针对性的判定与反馈。\n\n"
        "回答要求：\n"
        "- 先用一行明确结论：✅ 正确 / ⚠️ 部分正确 / ❌ 不正确，并简短点明关键判定依据。\n"
        "- 然后分条列出：哪里做对了、哪里出错或缺漏、应该如何改正。\n"
        "- 若题目本身有多种合理答案，请承认学习者的合理之处。\n"
        "- 直接以学习者的作答为对象，不要泛泛而谈。\n"
        "- 全程使用中文。"
    ),
    "en": (
        "You are a rigorous yet encouraging teaching assistant grading a learner's quiz answer. "
        "Use the question, reference answer, and explanation to deliver a targeted assessment.\n\n"
        "Requirements:\n"
        "- Open with one line that states the verdict: ✅ Correct / ⚠️ Partially correct / ❌ Incorrect, "
        "and the key reason.\n"
        "- Then list: what the learner got right, what is wrong or missing, and how to fix it.\n"
        "- If multiple reasonable answers exist, acknowledge what the learner did well.\n"
        "- Speak directly to the learner's submission — do not give a generic lecture.\n"
        "- Reply in English."
    ),
}


def _build_judge_user_prompt(
    *,
    language: str,
    question: str,
    question_type: str,
    options: dict | None,
    correct_answer: str,
    explanation: str,
    user_answer: str,
    has_image: bool,
    image_count: int = 0,
) -> str:
    options_block = ""
    if options:
        try:
            options_block = "\n".join(f"  {k}. {v}" for k, v in options.items())
        except Exception:
            options_block = ""
    if language == "zh":
        parts = [
            f"题目类型：{question_type or 'unknown'}",
            f"题干：\n{question}",
        ]
        if options_block:
            parts.append(f"选项：\n{options_block}")
        if correct_answer:
            parts.append(f"参考答案：\n{correct_answer}")
        if explanation:
            parts.append(f"参考解析：\n{explanation}")
        parts.append(
            "学习者作答：\n"
            + (
                user_answer.strip()
                if user_answer and user_answer.strip()
                else "（仅提交了图片，无文字作答）"
            )
        )
        if has_image:
            count_text = (
                f"学习者另附了 {image_count} 张图片作为作答内容"
                if image_count > 1
                else "学习者另附了一张图片作为作答内容"
            )
            parts.append(f"{count_text}，请结合图片中的文字/公式/草图一并判定。")
        parts.append("请针对该学习者的具体作答给出 AI 评判。")
    else:
        parts = [
            f"Question type: {question_type or 'unknown'}",
            f"Question:\n{question}",
        ]
        if options_block:
            parts.append(f"Options:\n{options_block}")
        if correct_answer:
            parts.append(f"Reference answer:\n{correct_answer}")
        if explanation:
            parts.append(f"Reference explanation:\n{explanation}")
        parts.append(
            "Learner's answer:\n"
            + (
                user_answer.strip()
                if user_answer and user_answer.strip()
                else "(only an image was submitted, no typed answer)"
            )
        )
        if has_image:
            if image_count > 1:
                parts.append(
                    f"The learner attached {image_count} images as part of the answer. "
                    "Read their text/formulas/sketches and factor them into the judgment."
                )
            else:
                parts.append(
                    "The learner attached an image as part of the answer. "
                    "Read its text/formulas/sketches and factor it into the judgment."
                )
        parts.append("Produce an AI judgment that addresses this learner's specific answer.")
    return "\n\n".join(parts)


async def _build_multimodal_user_content(
    *,
    text: str,
    image_records: list[dict[str, str]],
) -> list[dict[str, Any]]:
    """Compose an OpenAI-style content-parts array with text + image blocks.

    For ``url``-only records we resolve local AttachmentStore paths to
    base64 here (most providers can fetch external URLs themselves, but
    locally-hosted ``/api/attachments/...`` is only reachable from the
    browser). Falls back to passing the URL through when resolution is
    not possible.
    """
    from urllib.parse import unquote, urlparse

    from traittutor.services.storage import get_attachment_store

    content: list[dict[str, Any]] = [{"type": "text", "text": text}]
    attachment_store = get_attachment_store()
    resolve = getattr(attachment_store, "resolve_path", None)

    for record in image_records:
        b64 = record.get("base64") or ""
        url = record.get("url") or ""
        filename = record.get("filename") or "answer.png"
        mime_type = record.get("mime_type") or _guess_image_mime(filename)

        if not b64 and url and resolve is not None:
            try:
                parsed = urlparse(url)
                parts = (parsed.path or url).strip("/").split("/")
                # Expected shape: api/attachments/{sid}/{aid}/{name}
                if len(parts) >= 5 and parts[0] == "api" and parts[1] == "attachments":
                    sid = unquote(parts[2])
                    aid = unquote(parts[3])
                    name = unquote("/".join(parts[4:]))
                    target = resolve(session_id=sid, attachment_id=aid, filename=name)
                    if target is not None and target.exists():
                        b64 = _b64.b64encode(target.read_bytes()).decode("ascii")
            except Exception as exc:
                logger.debug("Could not resolve %s to bytes: %s", url, exc)

        if b64:
            data_url = f"data:{mime_type};base64,{b64}"
            content.append({"type": "image_url", "image_url": {"url": data_url}})
        elif url:
            content.append({"type": "image_url", "image_url": {"url": url}})

    return content


def _guess_image_mime(filename: str | None) -> str:
    if not filename:
        return "image/png"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "gif": "image/gif",
        "webp": "image/webp",
    }.get(ext, "image/png")


async def _stream_judge_response(
    *,
    user_prompt: str,
    system_prompt: str,
    image_records: list[dict[str, str]],
    cancellation_event: asyncio.Event | None = None,
) -> AsyncIterator[str]:
    """Yield Judge text through the selected server-owned LLM boundary.

    The Gateway path receives the reference answer only inside its
    server-side typed request.  It deliberately forwards only text events to
    the existing browser protocol: Judge owns neither tools nor a tool loop,
    and provider reasoning/usage receipts must not become learner content.
    """
    attachments = tuple(
        GatewayAttachment(
            type="image",
            filename=record.get("filename") or "answer.png",
            mime_type=record.get("mime_type") or "image/png",
            base64=record.get("base64") or None,
            url=record.get("url") or None,
        )
        for record in image_records
        if record.get("base64") or record.get("url")
    )
    request = GatewayRequest(
        prompt=user_prompt,
        system_prompt=system_prompt,
        purpose="quiz_judge",
        messages=(
            GatewayMessage(role="system", content=system_prompt),
            GatewayMessage(role="user", content=user_prompt),
        ),
        attachments=attachments,
        # The existing Judge surface has no client timeout input.  This
        # server-owned deadline prevents an orphaned WebSocket provider
        # call without shrinking normal multimodal judging to the old
        # interactive 12-second execution budget.
        timeout_seconds=120.0,
        cancellation_event=cancellation_event,
    )
    async for event in get_gateway().stream(request):
        if event.type == "text" and event.text:
            yield event.text
        elif event.type == "cancelled":
            return


async def _resolve_server_held_judge_item(
    data: dict[str, Any], *, store: Any | None = None
) -> tuple[str, str, str, dict[str, Any]] | None:
    """Resolve a judge target without accepting browser question/key fields."""
    session_id = str(data.get("session_id") or "").strip()
    turn_id = str(data.get("turn_id") or "").strip()
    question_id = str(data.get("question_id") or "").strip()
    if not session_id or not turn_id or not question_id:
        return None
    if store is None:
        from traittutor.services.session import get_sqlite_session_store

        store = get_sqlite_session_store()
    item = await store.get_server_quiz_item(session_id, turn_id, question_id)
    if not isinstance(item, dict):
        return None
    question = str(item.get("question") or "").strip()
    expected = str(item.get("correct_answer") or "").strip()
    if not question or not expected:
        return None
    return session_id, turn_id, question_id, item


def _record_canonical_judge_submission(
    data: dict[str, Any],
    *,
    user_id: str,
    user_answer: str,
    has_image: bool,
    chain: Any | None = None,
) -> Any | None:
    """Record AI-judge usage as ungraded, attribution-pending evidence."""
    from traittutor.learning.event_chain import CanonicalAnswerEventChain

    if not (user_answer.strip() or has_image):
        return None
    event_chain = chain or CanonicalAnswerEventChain()
    return event_chain.record_ungraded_submission(
        user_id=user_id,
        question_id=str(data.get("question_id") or data.get("question") or ""),
        # The boundary owns submission identity. Client retries should replay
        # their token; older clients receive a fresh token per submission.
        attempt_id=str(data.get("attempt_id") or f"attempt_{uuid4().hex}"),
        # Retained only as a partition hint on a weak event. Browser-provided
        # subject/KC/verdict fields never become reliable attribution.
        subject_id=str(data.get("subject_id") or ""),
    )


@router.websocket("/question/judge")
async def websocket_quiz_judge(websocket: WebSocket):
    """Stream an AI judgment for a single quiz answer.

    Auth is enforced via ``ws_require_auth`` rather than a router-level
    HTTP dependency — see module docstring.

    Client → Server (initial JSON):
        {
            "session_id": str,
            "turn_id": str,
            "question_id": str,
            "attempt_id": str,
            "user_answer": str,
            # Image entries. Each entry has either ``base64``
            # (no ``data:`` prefix) or ``url`` (already hosted via the
            # AttachmentStore). ``user_answer_image`` (single, base64) is
            # not accepted.
            "user_answer_images": [
                {"base64": str, "url": str, "filename": str, "mime_type": str},
                ...
            ] | null,
            "language": "zh" | "en",
        }

    Server → Client (streaming):
        {"type": "started"}
        {"type": "text", "content": "..."}        # zero or more
        {"type": "done"}
        {"type": "error", "content": "..."}
    """
    from traittutor.api.routers.auth import ws_auth_failed, ws_require_auth, ws_reset_auth

    user_token = await ws_require_auth(websocket)
    if user_token is ws_auth_failed:
        return

    await websocket.accept()

    async def safe_send(payload: dict[str, Any]) -> bool:
        try:
            await websocket.send_json(payload)
            return True
        except (WebSocketDisconnect, RuntimeError, ConnectionError):
            return False

    try:
        data = await websocket.receive_json()
    except WebSocketDisconnect:
        return
    except Exception as exc:
        await safe_send({"type": "error", "content": f"Invalid request: {exc}"})
        try:
            await websocket.close()
        except Exception:
            pass
        if user_token is not None:
            try:
                ws_reset_auth(user_token)
            except Exception:
                pass
        return

    resolved_item = await _resolve_server_held_judge_item(data)
    if resolved_item is None:
        # Do not distinguish a malformed identity from an absent/private item:
        # both must fail closed rather than revive browser-held answer keys.
        await safe_send(
            {
                "type": "error",
                "content": "Server-held quiz question not found; regenerate the quiz before judging.",
            }
        )
        try:
            await websocket.close()
        except Exception:
            pass
        if user_token is not None:
            try:
                ws_reset_auth(user_token)
            except Exception:
                pass
        return
    session_id, turn_id, question_id, server_item = resolved_item
    question_text = str(server_item.get("question") or "").strip()
    correct_answer = str(server_item.get("correct_answer") or "").strip()

    requested_language = (data.get("language") or "").strip().lower()
    if requested_language not in ("zh", "en"):
        requested_language = get_ui_language(
            default=_config.get("system", {}).get("language", "en")
        )
        if requested_language not in ("zh", "en"):
            requested_language = "en"

    user_answer = data.get("user_answer") or ""

    # Resolve the canonical image set.
    raw_images = data.get("user_answer_images")
    image_records: list[dict[str, str]] = []
    if isinstance(raw_images, list):
        for entry in raw_images:
            if not isinstance(entry, dict):
                continue
            b64 = entry.get("base64") or ""
            url = entry.get("url") or ""
            if isinstance(b64, str) and b64.startswith("data:"):
                try:
                    b64 = b64.split(",", 1)[1]
                except IndexError:
                    b64 = ""
            if not b64 and not url:
                continue
            filename = entry.get("filename") or "answer.png"
            mime_type = entry.get("mime_type") or _guess_image_mime(filename)
            image_records.append(
                {
                    "base64": b64,
                    "url": url,
                    "filename": filename,
                    "mime_type": mime_type,
                }
            )

    has_image = bool(image_records)

    from traittutor.multi_user.context import get_current_user

    # This is an AI feedback surface rather than a structured server verdict.
    # The canonical chain keeps the submission auditable but it can never
    # update BKT merely from a judgment response.
    _record_canonical_judge_submission(
        {
            "question_id": question_id,
            "attempt_id": str(data.get("attempt_id") or "").strip(),
        },
        user_id=get_current_user().id,
        user_answer=user_answer,
        has_image=has_image,
    )

    options_value = (
        server_item.get("options") if isinstance(server_item.get("options"), dict) else None
    )
    system_prompt = _JUDGE_SYSTEM_PROMPTS.get(requested_language, _JUDGE_SYSTEM_PROMPTS["en"])
    user_prompt = _build_judge_user_prompt(
        language=requested_language,
        question=question_text,
        question_type=str(server_item.get("question_type") or ""),
        options=options_value,
        correct_answer=correct_answer,
        explanation=str(server_item.get("explanation") or ""),
        user_answer=user_answer,
        has_image=has_image,
        image_count=len(image_records),
    )

    if not (user_answer.strip() or has_image):
        await safe_send(
            {
                "type": "error",
                "content": ("No answer to judge — submit a typed answer or attach an image."),
            }
        )
        try:
            await websocket.close()
        except Exception:
            pass
        if user_token is not None:
            try:
                ws_reset_auth(user_token)
            except Exception:
                pass
        return

    await safe_send({"type": "started"})

    cancellation_event = asyncio.Event()
    try:
        async for chunk in _stream_judge_response(
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            image_records=image_records,
            cancellation_event=cancellation_event,
        ):
            if not chunk:
                continue
            if not await safe_send({"type": "text", "content": chunk}):
                cancellation_event.set()
                break
        await safe_send({"type": "done"})
    except WebSocketDisconnect:
        logger.debug("AI judge client disconnected mid-stream")
    except Exception as exc:
        logger.exception("AI judge stream failed")
        await safe_send({"type": "error", "content": format_exception_message(exc)})
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
        if user_token is not None:
            try:
                ws_reset_auth(user_token)
            except Exception:
                pass
