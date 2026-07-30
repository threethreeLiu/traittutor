"""Shared SQLite coordination for durable TraitTutor generation tasks.

SQLite is deliberately the source of truth: every worker claims a queued job
inside ``BEGIN IMMEDIATE`` and holds a renewable lease.  Local asyncio tasks
only execute jobs already claimed in that shared store, so two API instances
cannot run the same generation concurrently.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
import json
import logging
from pathlib import Path
import sqlite3
import time
from typing import Any, Callable, Literal
from uuid import uuid4

from traittutor.multi_user.paths import SYSTEM_ROOT, scope_for_user
from traittutor.services.path_service import get_path_service

from .runner import GenerationConfigurationError, GenerationModelExhaustedError
from .service import GenerationRequest, GenerationResult, MaterialSource, generate_traittutor_content, generate_traittutor_content_async, save_generation

logger = logging.getLogger(__name__)

TaskStatus = Literal["queued", "running", "completed", "failed", "cancelled", "interrupted"]
TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "interrupted"})
DEFAULT_MAX_CONCURRENT_GENERATIONS_PER_USER = 2
DEFAULT_MAX_CONCURRENT_GENERATIONS = 8
DEFAULT_MAX_CONCURRENT_PER_MODEL = 2
_LEASE_SECONDS = 300
_LEASE_HEARTBEAT_SECONDS = 60


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _serialize_request(request: GenerationRequest) -> dict[str, Any]:
    return {"generation_type": request.generation_type, "material": {"source_type": request.material.source_type, "text": request.material.text, "title": request.material.title, "source_id": request.material.source_id, "metadata": request.material.metadata or {}}, "learner_profile": request.learner_profile or {}, "options": request.options or {}}


def _deserialize_request(payload: dict[str, Any]) -> GenerationRequest:
    material = dict(payload.get("material") or {})
    return GenerationRequest(generation_type=payload["generation_type"], material=MaterialSource(source_type=material["source_type"], text=str(material.get("text") or ""), title=str(material.get("title") or "Untitled material"), source_id=material.get("source_id"), metadata=dict(material.get("metadata") or {})), learner_profile=dict(payload.get("learner_profile") or {}), options=dict(payload.get("options") or {}))


def _provider_key(request: GenerationRequest) -> str:
    # ``runner`` can rotate primary -> backup models after enqueue.  A primary
    # model key would let tasks bypass a backup's limit after rotation, so all
    # automatic generation routes share one conservative route-pool slot.
    # This may queue a little earlier than per-model accounting, but never
    # oversubscribes a fallback provider/model.
    return "generate:rotating-route-pool"


@dataclass
class GenerationTask:
    generation_id: str
    owner_id: str
    request: GenerationRequest
    owner_username: str = "local"
    owner_role: str = "admin"
    status: TaskStatus = "queued"
    provider_key: str = "auto:default"
    events: list[dict[str, Any]] = field(default_factory=list)
    result: GenerationResult | None = None
    error: str | None = None
    error_code: str | None = None
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    started_at: str | None = None
    completed_at: str | None = None
    cancel_requested: bool = False
    wake: asyncio.Event = field(default_factory=asyncio.Event, repr=False, compare=False)
    runner: asyncio.Task[None] | None = field(default=None, repr=False, compare=False)

    @property
    def completed(self) -> bool: return self.status in TERMINAL_STATUSES
    @property
    def retryable(self) -> bool: return self.status == "interrupted" or (self.status == "failed" and self.error_code != "prompt_configuration_invalid")

    def emit(self, event_type: str, message: str, **data: Any) -> None:
        self.events.append({"sequence": len(self.events) + 1, "type": event_type, "message": message, "data": data})
        self.updated_at = _now(); self.wake.set()

    def to_record(self) -> dict[str, Any]:
        return {"generation_id": self.generation_id, "owner_id": self.owner_id, "owner_username": self.owner_username, "owner_role": self.owner_role, "request": _serialize_request(self.request), "status": self.status, "provider_key": self.provider_key, "events": self.events, "result": self.result.to_dict() if self.result else None, "error": self.error, "error_code": self.error_code, "created_at": self.created_at, "updated_at": self.updated_at, "started_at": self.started_at, "completed_at": self.completed_at, "cancel_requested": self.cancel_requested}

    @classmethod
    def from_record(cls, data: dict[str, Any]) -> "GenerationTask":
        result = data.get("result")
        task_status = str(data.get("status") or "failed")
        if task_status not in {"queued", "running", "completed", "failed", "cancelled", "interrupted"}: task_status = "failed"
        return cls(generation_id=str(data["generation_id"]), owner_id=str(data["owner_id"]), owner_username=str(data.get("owner_username") or "local"), owner_role=str(data.get("owner_role") or "user"), request=_deserialize_request(dict(data["request"])), status=task_status, provider_key=str(data.get("provider_key") or "auto:default"), events=list(data.get("events") or []), result=GenerationResult(**result) if isinstance(result, dict) else None, error=data.get("error"), error_code=data.get("error_code"), created_at=str(data.get("created_at") or _now()), updated_at=str(data.get("updated_at") or _now()), started_at=data.get("started_at"), completed_at=data.get("completed_at"), cancel_requested=bool(data.get("cancel_requested", False)))  # type: ignore[arg-type]


class _TaskStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path; db_path.parent.mkdir(parents=True, exist_ok=True); self._init()
    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=10, isolation_level=None); conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL"); conn.execute("PRAGMA busy_timeout=10000"); return conn
    def _init(self):
        with self._connect() as c:
            c.executescript("""CREATE TABLE IF NOT EXISTS generation_tasks (generation_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, provider_key TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, lease_owner TEXT, lease_until REAL, cancel_requested INTEGER NOT NULL DEFAULT 0); CREATE INDEX IF NOT EXISTS idx_generation_tasks_claim ON generation_tasks(status, created_at); CREATE INDEX IF NOT EXISTS idx_generation_tasks_limits ON generation_tasks(status, owner_id, provider_key, lease_until);""")
            columns = {row["name"] for row in c.execute("PRAGMA table_info(generation_tasks)")}
            if "cancel_requested" not in columns: c.execute("ALTER TABLE generation_tasks ADD COLUMN cancel_requested INTEGER NOT NULL DEFAULT 0")
    def put(self, task: GenerationTask, *, preserve_lease: bool = True) -> None:
        payload = json.dumps(task.to_record(), ensure_ascii=False)
        with self._connect() as c:
            c.execute("INSERT INTO generation_tasks(generation_id,owner_id,provider_key,status,payload,created_at,updated_at,cancel_requested) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(generation_id) DO UPDATE SET provider_key=excluded.provider_key,status=excluded.status,payload=excluded.payload,updated_at=excluded.updated_at,cancel_requested=MAX(generation_tasks.cancel_requested, excluded.cancel_requested)", (task.generation_id, task.owner_id, task.provider_key, task.status, payload, task.created_at, task.updated_at, int(task.cancel_requested)))
    def load(self, generation_id: str) -> GenerationTask | None:
        with self._connect() as c:
            row=c.execute("SELECT payload,cancel_requested FROM generation_tasks WHERE generation_id=?", (generation_id,)).fetchone()
        if not row:return None
        task=GenerationTask.from_record(json.loads(row["payload"])); task.cancel_requested=bool(row["cancel_requested"]); return task

    def request_cancel(self, generation_id: str) -> GenerationTask | None:
        """Atomically cancel queued work or request cancellation of leased work."""
        with self._connect() as c:
            c.execute("BEGIN IMMEDIATE"); row=c.execute("SELECT payload,status,cancel_requested FROM generation_tasks WHERE generation_id=?",(generation_id,)).fetchone()
            if not row: c.execute("COMMIT"); return None
            task=GenerationTask.from_record(json.loads(row["payload"])); task.cancel_requested=True
            if row["status"] == "queued":
                task.status="cancelled";task.error="Generation was cancelled.";task.error_code="generation_cancelled";task.completed_at=_now();task.emit("cancelled",task.error,code=task.error_code,retryable=False)
                updated=c.execute("UPDATE generation_tasks SET status='cancelled',payload=?,updated_at=?,cancel_requested=1 WHERE generation_id=? AND status='queued'",(json.dumps(task.to_record(),ensure_ascii=False),task.updated_at,generation_id))
                if updated.rowcount != 1: c.execute("ROLLBACK"); return self.load(generation_id)
            elif row["status"] == "running":
                task.emit("cancellation_requested","Cancellation requested; the active model call will stop at its next safe boundary.")
                c.execute("UPDATE generation_tasks SET payload=?,updated_at=?,cancel_requested=1 WHERE generation_id=? AND status='running'",(json.dumps(task.to_record(),ensure_ascii=False),task.updated_at,generation_id))
            c.execute("COMMIT"); return self.load(generation_id)

    def finalize(self, task: GenerationTask) -> GenerationTask:
        """Finish without letting a remote cancellation be overwritten."""
        with self._connect() as c:
            c.execute("BEGIN IMMEDIATE"); row=c.execute("SELECT cancel_requested FROM generation_tasks WHERE generation_id=?",(task.generation_id,)).fetchone()
            if row and row["cancel_requested"] and task.status not in {"cancelled","failed"}:
                task.cancel_requested=True;task.status="cancelled";task.error="Generation was cancelled.";task.error_code="generation_cancelled";task.completed_at=_now();task.emit("cancelled",task.error,code=task.error_code,retryable=False)
            task.updated_at=_now()
            c.execute("UPDATE generation_tasks SET status=?,payload=?,updated_at=?,lease_owner=NULL,lease_until=NULL,cancel_requested=? WHERE generation_id=?",(task.status,json.dumps(task.to_record(),ensure_ascii=False),task.updated_at,int(task.cancel_requested),task.generation_id));c.execute("COMMIT");return task
    def interrupt_expired_or_unleased(self) -> int:
        """Recover only stale work; another live instance may hold a lease."""
        with self._connect() as c:
            c.execute("BEGIN IMMEDIATE")
            rows=c.execute("SELECT generation_id,payload FROM generation_tasks WHERE status='running' AND (lease_until IS NULL OR lease_until < ?)", (time.time(),)).fetchall()
            for row in rows:
                task=GenerationTask.from_record(json.loads(row["payload"])); task.status="interrupted"; task.error="Generation was interrupted by a service restart. Retry to continue."; task.error_code="generation_interrupted"; task.completed_at=_now(); task.emit("interrupted", task.error, code=task.error_code, retryable=True)
                c.execute("UPDATE generation_tasks SET status=?,payload=?,updated_at=?,lease_owner=NULL,lease_until=NULL WHERE generation_id=?", (task.status,json.dumps(task.to_record(),ensure_ascii=False),task.updated_at,task.generation_id))
            c.execute("COMMIT"); return len(rows)
    def claim_next(self, instance_id: str, *, per_user: int, global_limit: int, per_model: int) -> GenerationTask | None:
        now=time.time()
        with self._connect() as c:
            c.execute("BEGIN IMMEDIATE")
            # A dead instance lease is explicit interruption, never a silent duplicate.
            expired=c.execute("SELECT generation_id,payload FROM generation_tasks WHERE status='running' AND lease_until < ?", (now,)).fetchall()
            for row in expired:
                task=GenerationTask.from_record(json.loads(row["payload"])); task.status="interrupted"; task.error="Generation worker lease expired. Retry to continue."; task.error_code="generation_interrupted"; task.completed_at=_now(); task.emit("interrupted",task.error,code=task.error_code,retryable=True)
                c.execute("UPDATE generation_tasks SET status=?,payload=?,updated_at=?,lease_owner=NULL,lease_until=NULL WHERE generation_id=?",(task.status,json.dumps(task.to_record(),ensure_ascii=False),task.updated_at,task.generation_id))
            if c.execute("SELECT count(*) FROM generation_tasks WHERE status='running' AND lease_until >= ?",(now,)).fetchone()[0] >= global_limit: c.execute("COMMIT"); return None
            for row in c.execute("SELECT generation_id,payload FROM generation_tasks WHERE status='queued' ORDER BY created_at").fetchall():
                task=GenerationTask.from_record(json.loads(row["payload"]))
                if c.execute("SELECT count(*) FROM generation_tasks WHERE status='running' AND lease_until >= ? AND owner_id=?",(now,task.owner_id)).fetchone()[0] >= per_user: continue
                if c.execute("SELECT count(*) FROM generation_tasks WHERE status='running' AND lease_until >= ? AND provider_key=?",(now,task.provider_key)).fetchone()[0] >= per_model: continue
                task.status="running"; task.started_at=task.started_at or _now(); task.updated_at=_now()
                claim = c.execute("UPDATE generation_tasks SET status='running',payload=?,updated_at=?,lease_owner=?,lease_until=? WHERE generation_id=? AND status='queued'",(json.dumps(task.to_record(),ensure_ascii=False),task.updated_at,instance_id,now+_LEASE_SECONDS,task.generation_id))
                if claim.rowcount == 1: c.execute("COMMIT"); return task
            c.execute("COMMIT"); return None
    def renew(self,generation_id:str,instance_id:str)->bool:
        with self._connect() as c:
            return c.execute("UPDATE generation_tasks SET lease_until=? WHERE generation_id=? AND status='running' AND lease_owner=?",(time.time()+_LEASE_SECONDS,generation_id,instance_id)).rowcount==1


class GenerationTaskManager:
    _instance: "GenerationTaskManager | None" = None
    def __init__(self, generator: Callable[[GenerationRequest], GenerationResult] = generate_traittutor_content, *, max_concurrent_per_user:int=DEFAULT_MAX_CONCURRENT_GENERATIONS_PER_USER, max_concurrent:int=DEFAULT_MAX_CONCURRENT_GENERATIONS, max_concurrent_per_model:int=DEFAULT_MAX_CONCURRENT_PER_MODEL, storage_root:Path|None=None):
        if min(max_concurrent_per_user,max_concurrent,max_concurrent_per_model)<1: raise ValueError("generation concurrency limits must be positive")
        self._generator=generator; self._per_user=max_concurrent_per_user; self._global=max_concurrent; self._per_model=max_concurrent_per_model; self._tasks:dict[str,GenerationTask]={}; self._instance_id=f"{uuid4().hex}"; self._started=False
        db_path=(storage_root / "traittutor" / "generation-tasks.sqlite") if storage_root else (SYSTEM_ROOT / "generation-tasks.sqlite")
        self._store=_TaskStore(db_path)
    @classmethod
    def get_instance(cls)->"GenerationTaskManager":
        if cls._instance is None: cls._instance=cls()
        return cls._instance
    async def start(self)->None:
        if self._started:return
        self._store.interrupt_expired_or_unleased(); self._started=True; self._schedule()
    def _persist(self,task:GenerationTask)->None: self._store.put(task)
    def create(self,request:GenerationRequest)->GenerationTask:
        from traittutor.multi_user.context import get_current_user
        user=get_current_user(); task=GenerationTask(uuid4().hex,user.id,request,owner_username=user.username,owner_role=user.role,provider_key=_provider_key(request)); task.emit("accepted","Generation request accepted",generation_id=task.generation_id); self._persist(task); self._tasks[task.generation_id]=task; self._started=True; self._schedule(); return task
    def _schedule(self)->None:
        while True:
            claimed=self._store.claim_next(self._instance_id,per_user=self._per_user,global_limit=self._global,per_model=self._per_model)
            if claimed is None:return
            # Keep the object returned from ``create`` live for compatibility,
            # while the database record remains the cross-instance authority.
            task=self._tasks.get(claimed.generation_id) or claimed
            task.status=claimed.status; task.started_at=claimed.started_at; task.updated_at=claimed.updated_at
            self._tasks[task.generation_id]=task; task.runner=asyncio.create_task(self._run(task),name=f"traittutor-generation-{task.generation_id}")
    async def _heartbeat(self,task:GenerationTask)->None:
        try:
            while True:
                await asyncio.sleep(_LEASE_HEARTBEAT_SECONDS)
                if not self._store.renew(task.generation_id,self._instance_id): return
        except asyncio.CancelledError: return
    async def _run(self,task:GenerationTask)->None:
        from traittutor.multi_user.context import reset_current_user,set_current_user
        from traittutor.multi_user.models import CurrentUser
        token=set_current_user(CurrentUser(id=task.owner_id,username=task.owner_username,role=task.owner_role if task.owner_role in {"admin","user"} else "user",scope=scope_for_user(task.owner_id,is_admin=task.owner_role=="admin")))
        heartbeat=asyncio.create_task(self._heartbeat(task))
        try:
            if self._store.load(task.generation_id).cancel_requested: raise asyncio.CancelledError
            task.emit("material_resolved","Material is ready for generation",source_type=task.request.material.source_type); task.emit("profile_strategy_ready","Learner teaching strategy is ready"); task.emit("generation_started","Generating structured learning content",generation_type=task.request.generation_type); self._persist(task)
            result=await generate_traittutor_content_async(task.request,generation_id=task.generation_id) if self._generator is generate_traittutor_content else await asyncio.to_thread(self._generator,task.request)
            # Provider calls are not safely force-killable.  Do not claim a
            # cancellation until the call returns and this boundary is reached.
            if self._store.load(task.generation_id).cancel_requested: raise asyncio.CancelledError
            if result.generation_id!=task.generation_id: result=replace(result,generation_id=task.generation_id)
            for event in result.events:
                if event.get("type")=="batch_validated":task.emit("batch_validated","Structured output batch validated",**dict(event.get("data") or {}))
            save_generation(result); task.result=result; task.status="completed"; task.completed_at=_now(); task.emit("completed","Generation completed",result_url=f"/api/v1/traittutor/generate/generations/{task.generation_id}")
        except asyncio.CancelledError:
            task.status="cancelled"; task.error="Generation was cancelled."; task.error_code="generation_cancelled"; task.completed_at=_now(); task.emit("cancelled",task.error,code=task.error_code,retryable=False)
        except Exception as exc:
            # Keep the user-facing failure safely generic, while retaining a
            # traceback for operators.  This is especially important for the
            # asynchronous worker: otherwise a failed structured generation
            # loses the only diagnostic evidence before the UI can report it.
            logger.exception(
                "Generation task failed id=%s type=%s error=%s",
                task.generation_id,
                task.request.generation_type,
                type(exc).__name__,
            )
            from traittutor.services.prompt import PromptLoadError
            if isinstance(exc, PromptLoadError):
                task.error="Generation prompt configuration is invalid. Please contact an administrator.";task.error_code="prompt_configuration_invalid";task.status="failed";task.completed_at=_now();task.emit("failed",task.error,code=task.error_code,retryable=False)
                return
            if isinstance(exc,GenerationConfigurationError):task.error="No generation model is configured. Open Model settings to continue.";task.error_code="model_configuration_required"
            elif isinstance(exc,GenerationModelExhaustedError):task.error="Configured models are temporarily unavailable. TraitTutor has tried the backup models; please try again later.";task.error_code="model_routes_exhausted"
            else:task.error="Generation could not be completed. Please retry; TraitTutor will automatically use another available model.";task.error_code="generation_failed"
            task.status="failed";task.completed_at=_now();task.emit("failed",task.error,code=task.error_code,retryable=True)
        finally:
            heartbeat.cancel(); self._store.finalize(task); task.wake.set(); reset_current_user(token); self._schedule()
    def get(self,generation_id:str)->GenerationTask|None:
        from traittutor.multi_user.context import get_current_user
        task=self._store.load(generation_id); user=get_current_user()
        if task is None or (not user.is_admin and task.owner_id!=user.id):return None
        old=self._tasks.get(generation_id)
        if old is not None and old.runner is not None and not old.runner.done(): return old
        if old is not None: task.wake=old.wake;task.runner=old.runner
        self._tasks[generation_id]=task; return task
    def cancel(self,generation_id:str)->GenerationTask|None:
        task=self.get(generation_id)
        if task is None or task.completed:return task
        updated=self._store.request_cancel(generation_id)
        if updated is not None:
            old=self._tasks.get(generation_id)
            if old is not None: updated.wake=old.wake;updated.runner=old.runner
            self._tasks[generation_id]=updated
        return updated
    def retry(self,generation_id:str)->GenerationTask|None:
        task=self.get(generation_id)
        if task is None or not task.retryable:return task
        task.status="queued";task.error=None;task.error_code=None;task.completed_at=None;task.emit("retry_queued","Generation retry queued",generation_id=task.generation_id);self._persist(task);self._schedule();return task
    async def events_after(self,generation_id:str,after_sequence:int=0):
        next_sequence=after_sequence+1
        while True:
            task=self.get(generation_id)
            if task is None:raise KeyError(generation_id)
            task.wake.clear(); available=[event for event in task.events if event["sequence"]>=next_sequence]
            for event in available:next_sequence=event["sequence"]+1;yield event
            if task.completed:return
            try:await asyncio.wait_for(task.wake.wait(),timeout=1.0)
            except asyncio.TimeoutError:pass


def get_generation_task_manager()->GenerationTaskManager:return GenerationTaskManager.get_instance()
__all__=["DEFAULT_MAX_CONCURRENT_GENERATIONS_PER_USER","GenerationTask","GenerationTaskManager","get_generation_task_manager"]
