"""
Knowledge Base API Router
=========================

Handles knowledge base CRUD operations, file uploads, and initialization.
"""

import asyncio
from datetime import datetime

from fastapi import (
    APIRouter,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import StreamingResponse

# Initialize logger with config
from traittutor.api.routers.knowledge._shared import (  # noqa: F401
    _current_kb_base_dir,
    _writable_kb,
)
from traittutor.api.routers.knowledge._shared import _shared_logger as logger
from traittutor.api.utils.progress_broadcaster import ProgressBroadcaster
from traittutor.api.utils.task_log_stream import get_task_stream_manager
from traittutor.knowledge.manager import KnowledgeBaseManager
from traittutor.knowledge.progress_tracker import ProgressTracker
from traittutor.multi_user.knowledge_access import (
    resolve_kb,
)

router = APIRouter()


@router.get("/tasks/{task_id}/stream")
async def stream_task_logs(task_id: str):
    """Stream task-specific logs for knowledge-base operations."""
    manager = get_task_stream_manager()
    manager.ensure_task(task_id)
    return StreamingResponse(
        manager.stream(task_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/{kb_name}/progress")
async def get_progress(kb_name: str):
    """Get initialization progress for a knowledge base"""
    try:
        resource = resolve_kb(kb_name)
        progress_tracker = ProgressTracker(resource.name, resource.base_dir)
        progress = progress_tracker.get_progress()

        if progress is None:
            return {"status": "not_started", "message": "Initialization not started"}

        return progress
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.post("/{kb_name}/progress/clear")
async def clear_progress(kb_name: str):
    """Clear progress file for a knowledge base (useful for stuck states)"""
    try:
        _, resolved_name, base_dir = _writable_kb(kb_name)
        progress_tracker = ProgressTracker(resolved_name, base_dir)
        progress_tracker.clear()
        return {"status": "success", "message": f"Progress cleared for {kb_name}"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.websocket("/{kb_name}/progress/ws")
async def websocket_progress(websocket: WebSocket, kb_name: str):
    """WebSocket endpoint for real-time progress updates"""
    from traittutor.api.routers.auth import ws_auth_failed, ws_require_auth, ws_reset_auth

    user_token = await ws_require_auth(websocket)
    if user_token is ws_auth_failed:
        return

    await websocket.accept()

    broadcaster = ProgressBroadcaster.get_instance()

    try:
        await broadcaster.connect(kb_name, websocket)

        base_dir = _current_kb_base_dir()
        progress_tracker = ProgressTracker(kb_name, base_dir)
        initial_progress = progress_tracker.get_progress()
        expected_task_id = websocket.query_params.get("task_id")

        try:
            kb_info = KnowledgeBaseManager(base_dir=str(base_dir)).get_info(kb_name)
            kb_is_ready = bool(kb_info.get("statistics", {}).get("rag_initialized"))
        except Exception:
            kb_is_ready = False

        # Fast path: no active task — send current state and close immediately
        # This prevents infinite polling loops for ready or legacy KBs.
        has_active_task = False
        if initial_progress:
            stage = initial_progress.get("stage")
            if stage not in ("completed", "error", None):
                ts = initial_progress.get("timestamp")
                if ts:
                    try:
                        age = (datetime.now() - datetime.fromisoformat(ts)).total_seconds()
                        has_active_task = age < 120
                    except Exception:
                        pass

        if not has_active_task and not expected_task_id:
            if kb_is_ready:
                await websocket.send_json(
                    {
                        "type": "progress",
                        "data": {
                            "stage": "completed",
                            "message": "Knowledge base is ready.",
                            "percent": 100,
                            "current": 1,
                            "total": 1,
                        },
                    }
                )
            else:
                await websocket.send_json(
                    {
                        "type": "progress",
                        "data": initial_progress
                        or {
                            "stage": "error",
                            "message": "Knowledge base needs reindex or initialization.",
                        },
                    }
                )
            return

        if initial_progress:
            stage = initial_progress.get("stage")
            timestamp = initial_progress.get("timestamp")
            progress_task_id = initial_progress.get("task_id")

            should_send = False
            if expected_task_id and progress_task_id and progress_task_id != expected_task_id:
                should_send = False
            elif stage == "error" or not kb_is_ready:
                should_send = True
            elif stage != "completed" and timestamp:
                try:
                    progress_time = datetime.fromisoformat(timestamp)
                    now = datetime.now()
                    age_seconds = (now - progress_time).total_seconds()
                    if age_seconds < 300:
                        should_send = True
                except Exception:
                    pass

            if should_send:
                await websocket.send_json({"type": "progress", "data": initial_progress})

        last_timestamp = initial_progress.get("timestamp") if initial_progress else None

        while True:
            try:
                try:
                    await asyncio.wait_for(websocket.receive_text(), timeout=1.0)
                except asyncio.TimeoutError:
                    current_progress = progress_tracker.get_progress()
                    if current_progress:
                        progress_task_id = current_progress.get("task_id")
                        if (
                            expected_task_id
                            and progress_task_id
                            and progress_task_id != expected_task_id
                        ):
                            continue
                        current_timestamp = current_progress.get("timestamp")
                        if current_timestamp != last_timestamp:
                            await websocket.send_json(
                                {"type": "progress", "data": current_progress}
                            )
                            last_timestamp = current_timestamp

                            if current_progress.get("stage") in ["completed", "error"]:
                                await asyncio.sleep(3)
                                break
                    continue

            except WebSocketDisconnect:
                break
            except Exception:
                break

    except Exception as e:
        logger.debug(f"Progress WS error: {e}")
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass
    finally:
        await broadcaster.disconnect(kb_name, websocket)
        try:
            await websocket.close()
        except Exception:
            pass
        if user_token is not None:
            try:
                ws_reset_auth(user_token)
            except Exception:
                pass
