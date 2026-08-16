"""Durable, cross-process idempotency store for courseware runs."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import json
from pathlib import Path
from threading import Lock, RLock
from typing import Iterator

from pydantic import BaseModel, ConfigDict, Field

from traittutor.multi_user.models import LOCAL_ADMIN_ID
from traittutor.services.path_service import get_path_service
from traittutor.unified_storage import SectionedRecordStore

from .agentic_contracts import (
    AgentNodeCheckpoint,
    AgentRosterManifest,
    AgentTaskGraphV2,
    CoursewareRunPolicy,
)
from .courseware_orchestrator import AgentTaskResult, OrchestratorRun
from .prompt_bundle import CoursewarePromptBundle, content_hash
from .task_graph import AgentTaskGraph


class OrchestratorRunStoreError(RuntimeError):
    """The durable run store cannot be read or updated safely."""


def stable_run_key(graph: AgentTaskGraph) -> str:
    """Identify generation-affecting work, including every immutable input ref."""
    canonical = json.dumps(
        graph.model_dump(mode="json", exclude={"created_at"}),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def stable_agentic_request_key(
    bundle: CoursewarePromptBundle,
    *,
    planner_contract: str,
    roster: AgentRosterManifest,
    policy: CoursewareRunPolicy,
    replan_iteration: int = 0,
    replan_reason_codes: tuple[str, ...] = (),
) -> str:
    """Claim Planner work before the first model call."""
    canonical = json.dumps(
        {
            "bundle_hash": content_hash(bundle),
            "planner_contract": planner_contract,
            "roster": roster.model_dump(mode="json"),
            "policy": policy.model_dump(mode="json"),
            "replan_iteration": replan_iteration,
            "replan_reason_codes": replan_reason_codes,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


class AgenticPlanReceipt(BaseModel):
    """Persisted Planner result; prompts and raw model output are never stored."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    request_key: str
    generation_run_id: str
    planner_contract: str
    graph: AgentTaskGraphV2
    logical_llm_calls: int = 1
    output_tokens: int = 0
    started_at_unix: float | None = None


@dataclass(frozen=True)
class AgenticBudgetUsage:
    logical_llm_calls: int = 0
    tool_calls: int = 0
    output_tokens: int = 0
    started_at_unix: float | None = None


class AgenticBudgetReservation(BaseModel):
    """Crash-durable charge reservation written before a paid operation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    reservation_id: str
    generation_run_id: str
    logical_llm_calls: int = Field(default=0, ge=0, le=1)
    tool_calls: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    started_at_unix: float = Field(gt=0)


class AgenticTaskCheckpointRecord(BaseModel):
    """Private crash-recovery state; never projected to the learner DTO."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    checkpoint_id: str
    run_key: str
    generation_run_id: str
    checkpoint: AgentNodeCheckpoint
    result: AgentTaskResult | None = None


class OrchestratorRunStore:
    """Replace-only JSON store with an OS lock around execution claims."""

    _SCHEMA_VERSION = 2

    # Retention bounds. Every write rewrites the whole section (replace_all),
    # so the store's cost grows with history; these caps keep growth bounded.
    # Receipt models carry no timestamp, so pruning is by deterministic key
    # order rather than true recency — the caps only bound growth, they are
    # not a recency policy.
    _MAX_RUNS = 200
    _MAX_PLANS = 200
    _MAX_CHECKPOINTS = 2000
    _MAX_BUDGET_RESERVATIONS = 2000

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._claim_locks: dict[str, Lock] = {}
        self._adapter = SectionedRecordStore(
            "orchestrator_runs",
            LOCAL_ADMIN_ID,
            schema_version=self._SCHEMA_VERSION,
            path_service=(get_path_service() if path == self._default_path() else None),
            legacy_path=path,
        )

    @staticmethod
    def _default_path() -> Path:
        return get_path_service().get_workspace_dir() / "traittutor" / "orchestrator-runs.json"

    @contextmanager
    def _file_lock(self, path: Path) -> Iterator[None]:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @contextmanager
    def execution_lock(self, run_key: str) -> Iterator[None]:
        """Hold the check-execute-persist claim across threads and processes."""
        with self._lock:
            thread_lock = self._claim_locks.setdefault(run_key, Lock())
        claim_path = self.path.with_name(f"{self.path.name}.{run_key}.lock")
        with thread_lock, self._file_lock(claim_path):
            yield

    def _load_unlocked(self) -> dict[str, OrchestratorRun]:
        try:
            payload = self._adapter.snapshot()
            # A missing "runs" key is a legitimate empty store; indexing
            # payload["runs"] directly raises an uncaught KeyError (not in the
            # except tuple). Resolve once via ``.get`` (parity with page_store).
            raw_runs = payload.get("runs", []) if isinstance(payload, dict) else None
            if not isinstance(raw_runs, list):
                raise ValueError("run store root is invalid")
            runs = [OrchestratorRun.model_validate(item) for item in raw_runs]
        except (OSError, ValueError) as exc:
            raise OrchestratorRunStoreError(
                f"orchestrator run store unreadable at {self.path}: {exc}"
            ) from exc
        if len({run.run_id for run in runs}) != len(runs):
            raise OrchestratorRunStoreError("orchestrator run store has duplicate run ids")
        return {run.run_id: run for run in runs}

    def get(self, run_id: str) -> OrchestratorRun | None:
        with self._lock:
            return self._load_unlocked().get(run_id)

    def get_by_key(self, run_key: str) -> OrchestratorRun | None:
        with self._lock:
            return next(
                (run for run in self._load_unlocked().values() if run.run_key == run_key),
                None,
            )

    def get_by_generation_run_id(self, generation_run_id: str) -> OrchestratorRun | None:
        """Read one externally addressable generation receipt from this owner store.

        Owner authorization is deliberately enforced by selecting an owner-scoped
        store before this method is called. A bounded replan legitimately persists
        two receipts for one generation (the pre-replan and the replanned run);
        the final run (highest ``replan_count``) is the one external callers
        address. Multiple runs with the same highest ``replan_count`` are still
        treated as corruption instead of selecting an arbitrary run.
        """
        with self._lock:
            matches = [
                run
                for run in self._load_unlocked().values()
                if run.generation_run_id == generation_run_id
            ]
        if not matches:
            return None
        best = max(matches, key=lambda item: item.replan_count)
        if sum(item.replan_count == best.replan_count for item in matches) > 1:
            raise OrchestratorRunStoreError(
                "orchestrator run store has duplicate generation run ids"
            )
        return best

    def save(self, run: OrchestratorRun) -> None:
        with self._lock, self._adapter.locked() as payload:
            runs = {
                run.run_id: run
                for run in (
                    OrchestratorRun.model_validate(item) for item in payload.get("runs", [])
                )
            }
            existing = runs.get(run.run_id)
            if existing is not None and existing != run:
                raise OrchestratorRunStoreError("run_id already has different immutable content")
            keyed = next((item for item in runs.values() if item.run_key == run.run_key), None)
            if keyed is not None and keyed.run_id != run.run_id:
                raise OrchestratorRunStoreError("run key already belongs to another run")
            runs[run.run_id] = run
            # A replanned run supersedes the pre-replan receipt for the same
            # generation: keep only the final run so one generation addresses
            # exactly one receipt (and get_by_generation_run_id stays unique).
            best_by_generation: dict[str, list[OrchestratorRun]] = {}
            for candidate in runs.values():
                current = best_by_generation.get(candidate.generation_run_id, [])
                if not current or candidate.replan_count > current[0].replan_count:
                    best_by_generation[candidate.generation_run_id] = [candidate]
                elif candidate.replan_count == current[0].replan_count:
                    # Preserve equal-rank collisions so the read path can fail
                    # closed instead of silently selecting an arbitrary trace.
                    current.append(candidate)
            runs = {
                candidate.run_id: candidate
                for candidates in best_by_generation.values()
                for candidate in candidates
            }
            if len(runs) > self._MAX_RUNS:
                runs = dict(sorted(runs.items())[-self._MAX_RUNS :])
            payload["runs"] = [item.model_dump(mode="json") for _, item in sorted(runs.items())]
            self._adapter.replace_all(payload)

    def get_plan(self, request_key: str) -> AgenticPlanReceipt | None:
        with self._lock:
            payload = self._adapter.snapshot()
            matches = [
                AgenticPlanReceipt.model_validate(item)
                for item in payload.get("plans", [])
                if item.get("request_key") == request_key
            ]
        if len(matches) > 1:
            raise OrchestratorRunStoreError("agentic request key has duplicate plans")
        return matches[0] if matches else None

    def save_plan(self, receipt: AgenticPlanReceipt) -> None:
        with self._lock, self._adapter.locked() as payload:
            plans = {
                item.request_key: item
                for item in (
                    AgenticPlanReceipt.model_validate(raw) for raw in payload.get("plans", [])
                )
            }
            existing = plans.get(receipt.request_key)
            if existing is not None and existing != receipt:
                raise OrchestratorRunStoreError(
                    "agentic request key already has a different immutable plan"
                )
            plans[receipt.request_key] = receipt
            if len(plans) > self._MAX_PLANS:
                plans = dict(sorted(plans.items())[-self._MAX_PLANS :])
            payload["plans"] = [item.model_dump(mode="json") for _, item in sorted(plans.items())]
            self._adapter.replace_all(payload)

    def get_task_checkpoints(self, run_key: str) -> dict[str, AgenticTaskCheckpointRecord]:
        with self._lock:
            payload = self._adapter.snapshot()
            records = [
                AgenticTaskCheckpointRecord.model_validate(item)
                for item in payload.get("checkpoints", [])
                if item.get("run_key") == run_key
            ]
        by_task = {item.checkpoint.task_id: item for item in records}
        if len(by_task) != len(records):
            raise OrchestratorRunStoreError("agentic run has duplicate task checkpoints")
        return by_task

    def save_task_checkpoint(self, record: AgenticTaskCheckpointRecord) -> None:
        with self._lock, self._adapter.locked() as payload:
            checkpoints = {
                item.checkpoint_id: item
                for item in (
                    AgenticTaskCheckpointRecord.model_validate(raw)
                    for raw in payload.get("checkpoints", [])
                )
            }
            checkpoints[record.checkpoint_id] = record
            if len(checkpoints) > self._MAX_CHECKPOINTS:
                checkpoints = dict(sorted(checkpoints.items())[-self._MAX_CHECKPOINTS :])
            payload["checkpoints"] = [
                item.model_dump(mode="json") for _, item in sorted(checkpoints.items())
            ]
            self._adapter.replace_all(payload)

    def reserve_agentic_budget(self, reservation: AgenticBudgetReservation) -> None:
        """Persist one operation charge before dispatch; token usage may fill in later."""
        with self._lock, self._adapter.locked() as payload:
            reservations = {
                item.reservation_id: item
                for item in (
                    AgenticBudgetReservation.model_validate(raw)
                    for raw in payload.get("budget_reservations", [])
                )
            }
            existing = reservations.get(reservation.reservation_id)
            if existing is not None:
                if (
                    existing.generation_run_id != reservation.generation_run_id
                    or existing.logical_llm_calls != reservation.logical_llm_calls
                    or existing.tool_calls != reservation.tool_calls
                    or existing.started_at_unix != reservation.started_at_unix
                    or reservation.output_tokens < existing.output_tokens
                ):
                    raise OrchestratorRunStoreError(
                        "agentic budget reservation changed immutable charge identity"
                    )
            reservations[reservation.reservation_id] = reservation
            if len(reservations) > self._MAX_BUDGET_RESERVATIONS:
                reservations = dict(sorted(reservations.items())[-self._MAX_BUDGET_RESERVATIONS :])
            payload["budget_reservations"] = [
                item.model_dump(mode="json") for _, item in sorted(reservations.items())
            ]
            self._adapter.replace_all(payload)

    def get_agentic_budget_usage(self, generation_run_id: str) -> AgenticBudgetUsage:
        """Aggregate every persisted Planner and Specialist charge for one run."""
        with self._lock:
            payload = self._adapter.snapshot()
            plans = [
                AgenticPlanReceipt.model_validate(item)
                for item in payload.get("plans", [])
                if item.get("generation_run_id") == generation_run_id
            ]
            checkpoints = [
                AgenticTaskCheckpointRecord.model_validate(item).checkpoint
                for item in payload.get("checkpoints", [])
                if item.get("generation_run_id") == generation_run_id
            ]
            reservations = [
                AgenticBudgetReservation.model_validate(item)
                for item in payload.get("budget_reservations", [])
                if item.get("generation_run_id") == generation_run_id
            ]
        if reservations:
            return AgenticBudgetUsage(
                logical_llm_calls=sum(item.logical_llm_calls for item in reservations),
                tool_calls=sum(item.tool_calls for item in reservations),
                output_tokens=sum(item.output_tokens for item in reservations),
                started_at_unix=min(item.started_at_unix for item in reservations),
            )
        starts = [item.started_at_unix for item in plans if item.started_at_unix is not None]
        return AgenticBudgetUsage(
            logical_llm_calls=sum(item.logical_llm_calls for item in plans)
            + sum(item.logical_llm_calls for item in checkpoints),
            tool_calls=sum(item.tool_calls for item in checkpoints),
            output_tokens=sum(item.output_tokens for item in plans)
            + sum(item.output_tokens for item in checkpoints),
            started_at_unix=min(starts) if starts else None,
        )


__all__ = [
    "AgenticBudgetReservation",
    "AgenticPlanReceipt",
    "AgenticBudgetUsage",
    "AgenticTaskCheckpointRecord",
    "OrchestratorRunStore",
    "OrchestratorRunStoreError",
    "stable_agentic_request_key",
    "stable_run_key",
]
