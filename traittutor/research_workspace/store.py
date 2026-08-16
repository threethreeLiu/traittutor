"""Owner-bound, file-locked truth store for Research Workspace product state."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
from typing import Any, Iterator, Literal, TypeVar, cast
from uuid import uuid4

from pydantic import BaseModel, ValidationError

from traittutor.services.path_service import get_path_service
from traittutor.unified_storage import SectionedRecordStore

from .models import (
    ResearchBrief,
    ResearchClaim,
    ResearchContinuationRef,
    ResearchKnowledgeBaseBinding,
    ResearchNote,
    ResearchReportArtifact,
    ResearchRun,
    ResearchRunStatus,
    ResearchSource,
    ResearchTaskReceipt,
    ResearchWorkspace,
    ResearchWorkspaceStatus,
)
from .source_validation import validate_report, validate_sources_and_claims
from .state_machine import require_transition

_STORE_SCHEMA_VERSION = 1
_ModelT = TypeVar("_ModelT", bound=BaseModel)
_FENCING_TRANSITIONS: frozenset[ResearchRunStatus] = frozenset(
    {"pausing", "paused", "cancelling", "cancelled", "queued"}
)


class ResearchWorkspaceStoreError(RuntimeError):
    """The durable workspace store cannot safely complete an operation."""


class ResearchWorkspaceVersionConflict(ResearchWorkspaceStoreError):
    """A workspace, note, or run CAS precondition is stale."""

    def __init__(self, *, expected_revision: int, actual_revision: int) -> None:
        self.expected_revision = expected_revision
        self.actual_revision = actual_revision
        super().__init__(f"expected revision {expected_revision}, found {actual_revision}")


class ResearchWorkspaceIdempotencyConflict(ResearchWorkspaceStoreError):
    """An idempotency key was replayed with different inputs."""


class ResearchRunLeaseUnavailable(ResearchWorkspaceStoreError):
    """A queued or expired run is not currently available to this worker."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class ResearchWorkspaceStore:
    """Append-only revisions and artifacts scoped to one authenticated owner."""

    def __init__(self, owner_id: str, *, path: Path | None = None) -> None:
        owner_id = owner_id.strip()
        if not owner_id:
            raise ValueError("owner_id is required")
        self.owner_id = owner_id
        self._store_path = path
        self._adapter = SectionedRecordStore(
            "research_workspaces",
            owner_id,
            schema_version=_STORE_SCHEMA_VERSION,
            path_service=get_path_service() if path is None else None,
            legacy_path=path,
        )

    def _path(self) -> Path:
        return self._store_path or (
            get_path_service().get_workspace_dir() / "traittutor" / "research_workspaces.json"
        )

    def _lock_path(self) -> Path:
        return self._path().with_suffix(".lock")

    @staticmethod
    def _empty() -> dict[str, Any]:
        return {
            "schema_version": _STORE_SCHEMA_VERSION,
            "workspaces": [],
            "briefs": [],
            "runs": [],
            "receipts": [],
            "sources": [],
            "notes": [],
            "claims": [],
            "reports": [],
            "operations": [],
        }

    def _load(self) -> dict[str, Any]:
        try:
            payload = self._adapter.snapshot()
        except Exception as exc:
            raise ResearchWorkspaceStoreError("unable to read research workspace data") from exc
        collection_keys = set(self._empty()) - {"schema_version"}
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != _STORE_SCHEMA_VERSION
            or any(not isinstance(payload.get(key), list) for key in collection_keys)
        ):
            raise ResearchWorkspaceStoreError("research workspace data has an invalid format")
        return payload

    @contextmanager
    def _locked(self) -> Iterator[dict[str, Any]]:
        with self._adapter.locked() as payload:
            yield payload

    def _models(
        self, payload: dict[str, Any], collection: str, model_type: type[_ModelT]
    ) -> list[_ModelT]:
        try:
            return [
                model_type.model_validate(record)
                for record in payload[collection]
                if isinstance(record, dict) and record.get("owner_id") == self.owner_id
            ]
        except ValidationError as exc:
            raise ResearchWorkspaceStoreError(
                f"research workspace {collection} data is invalid"
            ) from exc

    @staticmethod
    def _latest(records: list[_ModelT], revision_field: str) -> _ModelT | None:
        return max(records, key=lambda item: int(getattr(item, revision_field)), default=None)

    def _workspace(
        self, payload: dict[str, Any], workspace_id: str, *, revision: int | None = None
    ) -> ResearchWorkspace | None:
        records = [
            item
            for item in self._models(payload, "workspaces", ResearchWorkspace)
            if item.workspace_id == workspace_id and (revision is None or item.revision == revision)
        ]
        return self._latest(records, "revision")

    def _brief(
        self,
        payload: dict[str, Any],
        brief_id: str,
        *,
        version: int | None = None,
    ) -> ResearchBrief | None:
        records = [
            item
            for item in self._models(payload, "briefs", ResearchBrief)
            if item.brief_id == brief_id and (version is None or item.version == version)
        ]
        return self._latest(records, "version")

    def _run(
        self, payload: dict[str, Any], run_id: str, *, revision: int | None = None
    ) -> ResearchRun | None:
        records = [
            item
            for item in self._models(payload, "runs", ResearchRun)
            if item.run_id == run_id and (revision is None or item.revision == revision)
        ]
        # A lease renewal must be durably appended while keeping the public
        # lifecycle revision stable.  Select its latest operational revision
        # deterministically, rather than letting a heartbeat break a user's
        # pause/cancel CAS token.
        return max(
            records,
            key=lambda item: (item.revision, item.lease_revision),
            default=None,
        )

    def _source(
        self, payload: dict[str, Any], source_id: str, *, revision: int | None = None
    ) -> ResearchSource | None:
        records = [
            item
            for item in self._models(payload, "sources", ResearchSource)
            if item.source_id == source_id and (revision is None or item.revision == revision)
        ]
        return self._latest(records, "revision")

    def _claim(
        self, payload: dict[str, Any], claim_id: str, *, revision: int | None = None
    ) -> ResearchClaim | None:
        records = [
            item
            for item in self._models(payload, "claims", ResearchClaim)
            if item.claim_id == claim_id and (revision is None or item.revision == revision)
        ]
        return self._latest(records, "revision")

    def _report(
        self, payload: dict[str, Any], report_id: str, *, revision: int | None = None
    ) -> ResearchReportArtifact | None:
        records = [
            item
            for item in self._models(payload, "reports", ResearchReportArtifact)
            if item.report_id == report_id and (revision is None or item.revision == revision)
        ]
        return self._latest(records, "revision")

    @staticmethod
    def _key_hash(value: str) -> str:
        normalized = value.strip()
        if not normalized or len(normalized) > 160:
            raise ValueError("idempotency key must contain 1 to 160 characters")
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    @staticmethod
    def _content_hash(value: dict[str, Any]) -> str:
        canonical = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _replay_operation(
        self,
        payload: dict[str, Any],
        *,
        operation: str,
        idempotency_key: str,
        request_hash: str,
    ) -> dict[str, Any] | None:
        key_hash = self._key_hash(idempotency_key)
        record = next(
            (
                item
                for item in payload["operations"]
                if isinstance(item, dict)
                and item.get("owner_id") == self.owner_id
                and item.get("operation") == operation
                and item.get("key_hash") == key_hash
            ),
            None,
        )
        if record is None:
            return None
        if record.get("request_hash") != request_hash:
            raise ResearchWorkspaceIdempotencyConflict(
                "idempotency key reused with different research inputs"
            )
        result = record.get("result")
        if not isinstance(result, dict):
            raise ResearchWorkspaceStoreError("idempotency record has an invalid result")
        return result

    def _record_operation(
        self,
        payload: dict[str, Any],
        *,
        operation: str,
        idempotency_key: str,
        request_hash: str,
        result: dict[str, Any],
    ) -> None:
        payload["operations"].append(
            {
                "owner_id": self.owner_id,
                "operation": operation,
                "key_hash": self._key_hash(idempotency_key),
                "request_hash": request_hash,
                "result": result,
            }
        )

    def create_workspace(
        self,
        *,
        title: str,
        subject_id: str | None,
        idempotency_key: str,
        created_at: str | None = None,
    ) -> ResearchWorkspace:
        title = title.strip()
        request_hash = self._content_hash({"title": title, "subject_id": subject_id})
        with self._locked() as payload:
            replay = self._replay_operation(
                payload,
                operation="create_workspace",
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
            if replay is not None:
                workspace = self._workspace(
                    payload,
                    str(replay["workspace_id"]),
                    revision=int(replay["revision"]),
                )
                if workspace is None:
                    raise ResearchWorkspaceStoreError(
                        "workspace idempotency record references missing state"
                    )
                return workspace
            now = created_at or _now()
            workspace = ResearchWorkspace(
                workspace_id=f"rws_{uuid4().hex[:20]}",
                owner_id=self.owner_id,
                title=title,
                subject_id=subject_id,
                created_at=now,
                updated_at=now,
            )
            payload["workspaces"].append(workspace.model_dump(mode="json"))
            self._record_operation(
                payload,
                operation="create_workspace",
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                result={"workspace_id": workspace.workspace_id, "revision": workspace.revision},
            )
            self._adapter.replace_all(payload)
            return workspace

    def get_workspace(self, workspace_id: str) -> ResearchWorkspace | None:
        return self._workspace(self._load(), workspace_id)

    def list_workspaces(self) -> tuple[ResearchWorkspace, ...]:
        payload = self._load()
        ids = {
            workspace.workspace_id
            for workspace in self._models(payload, "workspaces", ResearchWorkspace)
        }
        current = [self._workspace(payload, workspace_id) for workspace_id in ids]
        return tuple(
            sorted(
                (item for item in current if item is not None and item.status != "deleted"),
                key=lambda item: item.updated_at,
                reverse=True,
            )
        )

    def update_workspace(
        self,
        workspace_id: str,
        *,
        expected_revision: int,
        idempotency_key: str,
        title: str | None = None,
        subject_id: str | None = None,
        status: ResearchWorkspaceStatus | None = None,
        updated_at: str | None = None,
    ) -> ResearchWorkspace:
        request_hash = self._content_hash(
            {
                "workspace_id": workspace_id,
                "expected_revision": expected_revision,
                "title": title,
                "subject_id": subject_id,
                "status": status,
            }
        )
        with self._locked() as payload:
            replay = self._replay_operation(
                payload,
                operation="update_workspace",
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
            if replay is not None:
                workspace = self._workspace(
                    payload,
                    workspace_id,
                    revision=int(replay["revision"]),
                )
                if workspace is None:
                    raise ResearchWorkspaceStoreError(
                        "workspace update replay references missing state"
                    )
                return workspace
            current = self._workspace(payload, workspace_id)
            if current is None:
                raise KeyError(workspace_id)
            if current.revision != expected_revision:
                raise ResearchWorkspaceVersionConflict(
                    expected_revision=expected_revision,
                    actual_revision=current.revision,
                )
            changes: dict[str, Any] = {
                "revision": current.revision + 1,
                "updated_at": updated_at or _now(),
            }
            if title is not None:
                changes["title"] = title.strip()
            if subject_id is not None:
                changes["subject_id"] = subject_id
            if status is not None:
                changes["status"] = status
            updated = ResearchWorkspace.model_validate(
                {**current.model_dump(mode="python"), **changes}
            )
            payload["workspaces"].append(updated.model_dump(mode="json"))
            self._record_operation(
                payload,
                operation="update_workspace",
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                result={"workspace_id": workspace_id, "revision": updated.revision},
            )
            self._adapter.replace_all(payload)
            return updated

    def save_brief(
        self,
        workspace_id: str,
        *,
        question: str,
        objectives: tuple[str, ...] = (),
        constraints: tuple[str, ...] = (),
        source_policy: str = "web",
        knowledge_base: ResearchKnowledgeBaseBinding | None = None,
        continuation: ResearchContinuationRef | None = None,
        expected_workspace_revision: int,
        idempotency_key: str,
        created_at: str | None = None,
    ) -> ResearchBrief:
        content = {
            "question": question.strip(),
            "objectives": objectives,
            "constraints": constraints,
            "source_policy": source_policy,
            "knowledge_base": (
                knowledge_base.model_dump(mode="json") if knowledge_base is not None else None
            ),
            "continuation": (
                continuation.model_dump(mode="json") if continuation is not None else None
            ),
        }
        content_hash = self._content_hash(content)
        request_hash = self._content_hash(
            {
                "workspace_id": workspace_id,
                "expected_workspace_revision": expected_workspace_revision,
                "content_hash": content_hash,
            }
        )
        with self._locked() as payload:
            replay = self._replay_operation(
                payload,
                operation="save_brief",
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
            if replay is not None:
                brief = self._brief(
                    payload,
                    str(replay["brief_id"]),
                    version=int(replay["version"]),
                )
                if brief is None:
                    raise ResearchWorkspaceStoreError(
                        "brief idempotency record references missing state"
                    )
                return brief
            workspace = self._workspace(payload, workspace_id)
            if workspace is None:
                raise KeyError(workspace_id)
            if workspace.revision != expected_workspace_revision:
                raise ResearchWorkspaceVersionConflict(
                    expected_revision=expected_workspace_revision,
                    actual_revision=workspace.revision,
                )
            previous = (
                self._brief(payload, workspace.active_brief_id)
                if workspace.active_brief_id is not None
                else None
            )
            now = created_at or _now()
            brief = ResearchBrief(
                brief_id=previous.brief_id if previous is not None else f"rb_{uuid4().hex[:20]}",
                workspace_id=workspace_id,
                owner_id=self.owner_id,
                version=(previous.version + 1) if previous is not None else 1,
                question=question.strip(),
                objectives=objectives,
                constraints=constraints,
                source_policy=cast(Literal["web", "knowledge_base", "mixed"], source_policy),
                knowledge_base=knowledge_base,
                continuation=continuation,
                content_hash=content_hash,
                created_at=now,
            )
            updated_workspace = workspace.model_copy(
                update={
                    "active_brief_id": brief.brief_id,
                    "revision": workspace.revision + 1,
                    "updated_at": now,
                }
            )
            payload["briefs"].append(brief.model_dump(mode="json"))
            payload["workspaces"].append(updated_workspace.model_dump(mode="json"))
            self._record_operation(
                payload,
                operation="save_brief",
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                result={"brief_id": brief.brief_id, "version": brief.version},
            )
            self._adapter.replace_all(payload)
            return brief

    def get_brief(self, brief_id: str, *, version: int | None = None) -> ResearchBrief | None:
        return self._brief(self._load(), brief_id, version=version)

    def list_briefs(self, workspace_id: str) -> tuple[ResearchBrief, ...]:
        """Return immutable brief versions for one owner-bound workspace."""
        briefs = [
            brief
            for brief in self._models(self._load(), "briefs", ResearchBrief)
            if brief.workspace_id == workspace_id
        ]
        return tuple(sorted(briefs, key=lambda brief: (brief.version, brief.created_at)))

    def create_run(
        self,
        workspace_id: str,
        *,
        brief_id: str,
        brief_version: int,
        input_hash: str,
        idempotency_key: str,
        created_at: str | None = None,
    ) -> ResearchRun:
        request_hash = self._content_hash(
            {
                "workspace_id": workspace_id,
                "brief_id": brief_id,
                "brief_version": brief_version,
                "input_hash": input_hash,
            }
        )
        with self._locked() as payload:
            replay = self._replay_operation(
                payload,
                operation=f"create_run:{workspace_id}",
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
            if replay is not None:
                run = self._run(payload, str(replay["run_id"]), revision=1)
                if run is None:
                    raise ResearchWorkspaceStoreError(
                        "run idempotency record references missing state"
                    )
                return run
            workspace = self._workspace(payload, workspace_id)
            brief = self._brief(payload, brief_id, version=brief_version)
            if workspace is None or brief is None or brief.workspace_id != workspace_id:
                raise KeyError(workspace_id)
            if brief.content_hash != input_hash:
                raise ValueError("run input_hash must match the frozen brief")
            now = created_at or _now()
            run = ResearchRun(
                run_id=f"rr_{uuid4().hex[:20]}",
                workspace_id=workspace_id,
                owner_id=self.owner_id,
                brief_id=brief_id,
                brief_version=brief_version,
                input_hash=input_hash,
                idempotency_key=idempotency_key,
                status="queued",
                created_at=now,
                updated_at=now,
            )
            payload["runs"].append(run.model_dump(mode="json"))
            self._record_operation(
                payload,
                operation=f"create_run:{workspace_id}",
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                result={"run_id": run.run_id},
            )
            self._adapter.replace_all(payload)
            return run

    def get_run(self, run_id: str) -> ResearchRun | None:
        return self._run(self._load(), run_id)

    def list_runs(self, workspace_id: str | None = None) -> tuple[ResearchRun, ...]:
        payload = self._load()
        ids = {
            run.run_id
            for run in self._models(payload, "runs", ResearchRun)
            if workspace_id is None or run.workspace_id == workspace_id
        }
        current = [self._run(payload, run_id) for run_id in ids]
        return tuple(
            sorted(
                (item for item in current if item is not None),
                key=lambda item: item.updated_at,
                reverse=True,
            )
        )

    def transition_run(
        self,
        run_id: str,
        target: ResearchRunStatus,
        *,
        expected_revision: int,
        idempotency_key: str,
        updated_at: str | None = None,
    ) -> ResearchRun:
        request_hash = self._content_hash(
            {
                "run_id": run_id,
                "target": target,
                "expected_revision": expected_revision,
            }
        )
        with self._locked() as payload:
            replay = self._replay_operation(
                payload,
                operation=f"transition_run:{run_id}",
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
            if replay is not None:
                run = self._run(payload, run_id, revision=int(replay["revision"]))
                if run is None:
                    raise ResearchWorkspaceStoreError(
                        "run transition replay references missing state"
                    )
                return run
            current = self._run(payload, run_id)
            if current is None:
                raise KeyError(run_id)
            if current.revision != expected_revision:
                raise ResearchWorkspaceVersionConflict(
                    expected_revision=expected_revision,
                    actual_revision=current.revision,
                )
            require_transition(current.status, target)
            advance_fence = target in _FENCING_TRANSITIONS
            changes: dict[str, Any] = {
                "status": target,
                "revision": current.revision + 1,
                "lease_revision": current.lease_revision + int(advance_fence),
                "fencing_epoch": current.fencing_epoch + int(advance_fence),
                "updated_at": updated_at or _now(),
            }
            if advance_fence:
                changes.update({"claim_token": None, "claimed_by": None, "lease_expires_at": None})
            # A failure reason is meaningful only while a run is actually
            # failed/needs_review.  Any explicit lifecycle transition away from
            # those states (e.g. a user-initiated retry) must drop the stale
            # ``executor_failed`` receipt so a later completed run never shows
            # a green "completed" badge next to a red failure string.
            if current.failure_reason is not None:
                changes["failure_reason"] = None
            updated = current.model_copy(update=changes)
            payload["runs"].append(updated.model_dump(mode="json"))
            self._record_operation(
                payload,
                operation=f"transition_run:{run_id}",
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                result={"revision": updated.revision},
            )
            self._adapter.replace_all(payload)
            return updated

    def claim_run(
        self,
        run_id: str,
        *,
        worker_id: str,
        lease_seconds: int,
        now: str | None = None,
    ) -> ResearchRun:
        if lease_seconds < 1 or lease_seconds > 3600:
            raise ValueError("lease_seconds must be between 1 and 3600")
        worker_id = worker_id.strip()
        if not worker_id:
            raise ValueError("worker_id is required")
        now_value = now or _now()
        now_dt = _parse_utc(now_value)
        with self._locked() as payload:
            current = self._run(payload, run_id)
            if current is None:
                raise KeyError(run_id)
            expired = (
                current.status == "running"
                and current.lease_expires_at is not None
                and _parse_utc(current.lease_expires_at) <= now_dt
            )
            if current.status != "queued" and not expired:
                raise ResearchRunLeaseUnavailable("run is not queued or lease-expired")
            epoch = current.fencing_epoch + int(expired)
            claimed = current.model_copy(
                update={
                    "status": "running",
                    "revision": current.revision + 1,
                    "lease_revision": current.lease_revision + 1,
                    "fencing_epoch": epoch,
                    "claim_token": f"claim_{uuid4().hex}",
                    "claimed_by": worker_id,
                    "lease_expires_at": (now_dt + timedelta(seconds=lease_seconds)).isoformat(),
                    "updated_at": now_value,
                }
            )
            payload["runs"].append(claimed.model_dump(mode="json"))
            self._adapter.replace_all(payload)
            return claimed

    def renew_run_lease(
        self,
        run_id: str,
        *,
        worker_id: str,
        input_hash: str,
        fencing_epoch: int,
        claim_token: str,
        lease_seconds: int,
        now: str | None = None,
    ) -> ResearchRun:
        """Extend only the current owner's live claim without changing CAS state.

        A renewal is deliberately fenced on all executor identity fields.  It
        cannot reclaim an expired lease, revive a cancelled run, or overwrite a
        newer worker's lease.  ``lease_revision`` makes the append-only update
        observable without making an in-flight heartbeat look like a product
        lifecycle edit to the user.
        """

        if lease_seconds < 1 or lease_seconds > 3600:
            raise ValueError("lease_seconds must be between 1 and 3600")
        worker_id = worker_id.strip()
        if not worker_id:
            raise ValueError("worker_id is required")
        now_value = now or _now()
        now_dt = _parse_utc(now_value)
        with self._locked() as payload:
            current = self._run(payload, run_id)
            if current is None:
                raise KeyError(run_id)
            if current.claimed_by != worker_id or not self._is_current_claim(
                current,
                input_hash=input_hash,
                fencing_epoch=fencing_epoch,
                claim_token=claim_token,
                now=now_value,
            ):
                raise ResearchRunLeaseUnavailable("run claim is no longer current")
            renewed = current.model_copy(
                update={
                    "lease_revision": current.lease_revision + 1,
                    "lease_expires_at": (now_dt + timedelta(seconds=lease_seconds)).isoformat(),
                    "updated_at": now_value,
                }
            )
            payload["runs"].append(renewed.model_dump(mode="json"))
            self._adapter.replace_all(payload)
            return renewed

    def finalize_requested_lifecycle(
        self,
        run_id: str,
        *,
        updated_at: str | None = None,
    ) -> ResearchRun:
        """Settle a worker-observed pause/cancel request exactly once.

        The initiating control transition has already advanced the fencing
        epoch and cleared its claim.  This follow-up only completes that
        requested lifecycle; it never accepts a late executor result or
        reclaims work.
        """

        final_status: dict[ResearchRunStatus, ResearchRunStatus] = {
            "pausing": "paused",
            "cancelling": "cancelled",
        }
        with self._locked() as payload:
            current = self._run(payload, run_id)
            if current is None:
                raise KeyError(run_id)
            target = final_status.get(current.status)
            if target is None:
                return current
            require_transition(current.status, target)
            completed = current.model_copy(
                update={
                    "status": target,
                    "revision": current.revision + 1,
                    "updated_at": updated_at or _now(),
                    "claim_token": None,
                    "claimed_by": None,
                    "lease_expires_at": None,
                    # A settled pause/cancel must not keep surfacing a prior
                    # failure reason on a non-failed state.
                    "failure_reason": None,
                }
            )
            payload["runs"].append(completed.model_dump(mode="json"))
            self._adapter.replace_all(payload)
            return completed

    def list_pending_control_runs(self) -> tuple[ResearchRun, ...]:
        """Return only this owner's durable pause/cancel requests."""

        return tuple(run for run in self.list_runs() if run.status in {"pausing", "cancelling"})

    def list_claimable_runs(self, *, now: str | None = None) -> tuple[ResearchRun, ...]:
        now_dt = _parse_utc(now or _now())
        return tuple(
            run
            for run in self.list_runs()
            if run.status == "queued"
            or (
                run.status == "running"
                and run.lease_expires_at is not None
                and _parse_utc(run.lease_expires_at) <= now_dt
            )
        )

    def _accepted_receipt_for_task(
        self,
        payload: dict[str, Any],
        *,
        run_id: str,
        task_id: str,
        input_hash: str,
        fencing_epoch: int,
    ) -> ResearchTaskReceipt | None:
        return next(
            (
                item
                for item in self._models(payload, "receipts", ResearchTaskReceipt)
                if item.run_id == run_id
                and item.task_id == task_id
                and item.input_hash == input_hash
                and item.fencing_epoch == fencing_epoch
                and item.outcome == "accepted"
            ),
            None,
        )

    def _receipt_for_attempt(
        self,
        payload: dict[str, Any],
        *,
        run_id: str,
        task_id: str,
        input_hash: str,
        fencing_epoch: int,
    ) -> ResearchTaskReceipt | None:
        return next(
            (
                item
                for item in self._models(payload, "receipts", ResearchTaskReceipt)
                if item.run_id == run_id
                and item.task_id == task_id
                and item.input_hash == input_hash
                and item.fencing_epoch == fencing_epoch
            ),
            None,
        )

    def _is_current_claim(
        self,
        run: ResearchRun,
        *,
        input_hash: str,
        fencing_epoch: int,
        claim_token: str,
        now: str,
    ) -> bool:
        return bool(
            run.status == "running"
            and run.input_hash == input_hash
            and run.fencing_epoch == fencing_epoch
            and run.claim_token == claim_token
            and run.lease_expires_at is not None
            and _parse_utc(run.lease_expires_at) > _parse_utc(now)
        )

    def commit_task_result(
        self,
        run_id: str,
        *,
        task_id: str,
        input_hash: str,
        fencing_epoch: int,
        claim_token: str,
        sources: tuple[ResearchSource, ...],
        claims: tuple[ResearchClaim, ...],
        report: ResearchReportArtifact,
        final_status: ResearchRunStatus,
        created_at: str | None = None,
    ) -> ResearchTaskReceipt:
        if final_status not in {"completed", "needs_review"}:
            raise ValueError("successful task result must complete or require review")
        now = created_at or _now()
        with self._locked() as payload:
            existing = self._accepted_receipt_for_task(
                payload,
                run_id=run_id,
                task_id=task_id,
                input_hash=input_hash,
                fencing_epoch=fencing_epoch,
            )
            if existing is not None:
                return existing
            attempt = self._receipt_for_attempt(
                payload,
                run_id=run_id,
                task_id=task_id,
                input_hash=input_hash,
                fencing_epoch=fencing_epoch,
            )
            if attempt is not None:
                return attempt
            run = self._run(payload, run_id)
            if run is None:
                raise KeyError(run_id)
            if not self._is_current_claim(
                run,
                input_hash=input_hash,
                fencing_epoch=fencing_epoch,
                claim_token=claim_token,
                now=now,
            ):
                receipt = ResearchTaskReceipt(
                    receipt_id=f"rtr_{uuid4().hex[:20]}",
                    workspace_id=run.workspace_id,
                    run_id=run_id,
                    owner_id=self.owner_id,
                    task_id=task_id,
                    input_hash=input_hash,
                    fencing_epoch=fencing_epoch,
                    outcome="discarded_stale",
                    detail="claim_not_current",
                    created_at=now,
                )
                payload["receipts"].append(receipt.model_dump(mode="json"))
                self._adapter.replace_all(payload)
                return receipt
            existing_source_ids = {
                source.source_id
                for source in self._models(payload, "sources", ResearchSource)
                if source.workspace_id == run.workspace_id
            }
            validate_sources_and_claims(
                sources=sources,
                claims=claims,
                workspace_id=run.workspace_id,
                run_id=run_id,
                owner_id=self.owner_id,
                existing_source_ids=existing_source_ids,
            )
            claim_ids = {
                claim.claim_id
                for claim in self._models(payload, "claims", ResearchClaim)
                if claim.run_id == run_id
            } | {claim.claim_id for claim in claims}
            validate_report(
                report,
                workspace_id=run.workspace_id,
                run_id=run_id,
                owner_id=self.owner_id,
                claim_ids=claim_ids,
            )
            require_transition(run.status, final_status)
            receipt = ResearchTaskReceipt(
                receipt_id=f"rtr_{uuid4().hex[:20]}",
                workspace_id=run.workspace_id,
                run_id=run_id,
                owner_id=self.owner_id,
                task_id=task_id,
                input_hash=input_hash,
                fencing_epoch=fencing_epoch,
                outcome="accepted",
                created_at=now,
            )
            completed = run.model_copy(
                update={
                    "status": final_status,
                    "revision": run.revision + 1,
                    "claim_token": None,
                    "claimed_by": None,
                    "lease_expires_at": None,
                    # Clear a prior failure reason so a run that succeeded after
                    # a retry never renders "completed" with a stale red
                    # executor_failed string.  ``needs_review`` derives its own
                    # meaning from requires_review, not from this field.
                    "failure_reason": None,
                    "updated_at": now,
                }
            )
            payload["receipts"].append(receipt.model_dump(mode="json"))
            payload["sources"].extend(source.model_dump(mode="json") for source in sources)
            payload["claims"].extend(claim.model_dump(mode="json") for claim in claims)
            payload["reports"].append(report.model_dump(mode="json"))
            payload["runs"].append(completed.model_dump(mode="json"))
            self._adapter.replace_all(payload)
            return receipt

    def record_task_failure(
        self,
        run_id: str,
        *,
        task_id: str,
        input_hash: str,
        fencing_epoch: int,
        claim_token: str,
        created_at: str | None = None,
    ) -> ResearchTaskReceipt:
        now = created_at or _now()
        with self._locked() as payload:
            existing = self._accepted_receipt_for_task(
                payload,
                run_id=run_id,
                task_id=task_id,
                input_hash=input_hash,
                fencing_epoch=fencing_epoch,
            )
            if existing is not None:
                return existing
            attempt = self._receipt_for_attempt(
                payload,
                run_id=run_id,
                task_id=task_id,
                input_hash=input_hash,
                fencing_epoch=fencing_epoch,
            )
            if attempt is not None:
                return attempt
            run = self._run(payload, run_id)
            if run is None:
                raise KeyError(run_id)
            current = self._is_current_claim(
                run,
                input_hash=input_hash,
                fencing_epoch=fencing_epoch,
                claim_token=claim_token,
                now=now,
            )
            receipt = ResearchTaskReceipt(
                receipt_id=f"rtr_{uuid4().hex[:20]}",
                workspace_id=run.workspace_id,
                run_id=run_id,
                owner_id=self.owner_id,
                task_id=task_id,
                input_hash=input_hash,
                fencing_epoch=fencing_epoch,
                outcome="failed" if current else "discarded_stale",
                detail="executor_failed" if current else "claim_not_current",
                created_at=now,
            )
            payload["receipts"].append(receipt.model_dump(mode="json"))
            if current:
                require_transition(run.status, "failed")
                failed = run.model_copy(
                    update={
                        "status": "failed",
                        "revision": run.revision + 1,
                        "claim_token": None,
                        "claimed_by": None,
                        "lease_expires_at": None,
                        "failure_reason": "executor_failed",
                        "updated_at": now,
                    }
                )
                payload["runs"].append(failed.model_dump(mode="json"))
            self._adapter.replace_all(payload)
            return receipt

    def list_receipts(self, run_id: str) -> tuple[ResearchTaskReceipt, ...]:
        return tuple(
            receipt
            for receipt in self._models(self._load(), "receipts", ResearchTaskReceipt)
            if receipt.run_id == run_id
        )

    def list_sources(self, workspace_id: str) -> tuple[ResearchSource, ...]:
        payload = self._load()
        source_ids = {
            source.source_id
            for source in self._models(payload, "sources", ResearchSource)
            if source.workspace_id == workspace_id
        }
        current = [self._source(payload, source_id) for source_id in source_ids]
        return tuple(
            sorted(
                (source for source in current if source is not None),
                key=lambda source: (source.retrieved_at, source.source_id),
            )
        )

    def list_claims(self, run_id: str) -> tuple[ResearchClaim, ...]:
        payload = self._load()
        claim_ids = {
            claim.claim_id
            for claim in self._models(payload, "claims", ResearchClaim)
            if claim.run_id == run_id
        }
        current = [self._claim(payload, claim_id) for claim_id in claim_ids]
        return tuple(
            sorted(
                (claim for claim in current if claim is not None),
                key=lambda claim: (claim.created_at, claim.claim_id),
            )
        )

    def get_report(self, run_id: str) -> ResearchReportArtifact | None:
        payload = self._load()
        reports = [
            report
            for report in self._models(payload, "reports", ResearchReportArtifact)
            if report.run_id == run_id
        ]
        if not reports:
            return None
        # A source invalidation appends a revised projection with the same
        # report id.  Payload order is the durable append order, so resolving
        # the latest entry keeps the historical body accessible in the audit
        # ledger without serving stale evidence status.
        report_ids = {report.report_id for report in reports}
        return max(
            (self._report(payload, report_id) for report_id in report_ids),
            key=lambda report: (
                (report.created_at, report.revision) if report is not None else ("", 0)
            ),
            default=None,
        )

    def invalidate_source(
        self,
        workspace_id: str,
        source_id: str,
        *,
        expected_revision: int,
        idempotency_key: str,
        reason: str | None = None,
        invalidated_at: str | None = None,
    ) -> ResearchSource:
        """Append an evidence invalidation and its review projections atomically.

        This is deliberately not a delete: sources, claim text and report body
        remain in the ledger.  Only grounded claims that cite the source, and
        reports that include those claims, become ``needs_review``.
        """

        normalized_reason = reason.strip() if reason is not None else None
        request_hash = self._content_hash(
            {
                "workspace_id": workspace_id,
                "source_id": source_id,
                "expected_revision": expected_revision,
                "reason": normalized_reason,
            }
        )
        with self._locked() as payload:
            replay = self._replay_operation(
                payload,
                operation=f"invalidate_source:{workspace_id}:{source_id}",
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
            if replay is not None:
                source = self._source(payload, source_id, revision=int(replay["revision"]))
                if source is None:
                    raise ResearchWorkspaceStoreError(
                        "source invalidation replay references missing state"
                    )
                return source

            workspace = self._workspace(payload, workspace_id)
            if workspace is None:
                raise KeyError(workspace_id)
            if workspace.status != "active":
                raise ValueError("cannot invalidate a source in a non-active workspace")
            source = self._source(payload, source_id)
            if source is None or source.workspace_id != workspace_id:
                raise KeyError(source_id)
            if source.revision != expected_revision:
                raise ResearchWorkspaceVersionConflict(
                    expected_revision=expected_revision,
                    actual_revision=source.revision,
                )
            if source.status != "active":
                raise ValueError("source is not active")

            now = invalidated_at or _now()
            invalidated = source.model_copy(
                update={
                    "revision": source.revision + 1,
                    "status": "invalidated",
                    "invalidated_at": now,
                    "invalidation_reason": normalized_reason or None,
                }
            )
            all_claims = self._models(payload, "claims", ResearchClaim)
            current_claims = {
                claim_id: self._claim(payload, claim_id)
                for claim_id in {claim.claim_id for claim in all_claims}
            }
            affected_claims = tuple(
                claim
                for claim in current_claims.values()
                if claim is not None
                and claim.workspace_id == workspace_id
                and claim.kind == "grounded"
                and source_id in claim.source_ids
            )
            revised_claims = tuple(
                claim.model_copy(
                    update={
                        "revision": claim.revision + 1,
                        "evidence_status": "needs_review",
                        "review_required_source_ids": tuple(
                            sorted(set(claim.review_required_source_ids) | {source_id})
                        ),
                        "evidence_status_updated_at": now,
                    }
                )
                for claim in affected_claims
            )
            affected_claim_ids = {claim.claim_id for claim in affected_claims}
            all_reports = self._models(payload, "reports", ResearchReportArtifact)
            current_reports = {
                report_id: self._report(payload, report_id)
                for report_id in {report.report_id for report in all_reports}
            }
            revised_reports = tuple(
                report.model_copy(
                    update={
                        "revision": report.revision + 1,
                        "evidence_status": "needs_review",
                        "review_required_source_ids": tuple(
                            sorted(set(report.review_required_source_ids) | {source_id})
                        ),
                        "evidence_status_updated_at": now,
                    }
                )
                for report in current_reports.values()
                if report is not None
                and report.workspace_id == workspace_id
                and bool(set(report.claim_ids) & affected_claim_ids)
            )
            # One lock and one atomic replace make the source, dependent claim
            # and report statuses an all-or-nothing truth transition.
            payload["sources"].append(invalidated.model_dump(mode="json"))
            payload["claims"].extend(claim.model_dump(mode="json") for claim in revised_claims)
            payload["reports"].extend(report.model_dump(mode="json") for report in revised_reports)
            self._record_operation(
                payload,
                operation=f"invalidate_source:{workspace_id}:{source_id}",
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                result={"revision": invalidated.revision},
            )
            self._adapter.replace_all(payload)
            return invalidated

    def create_note(
        self,
        workspace_id: str,
        *,
        body: str,
        source_ids: tuple[str, ...] = (),
        idempotency_key: str,
        created_at: str | None = None,
    ) -> ResearchNote:
        requested_source_ids = source_ids
        request_hash = self._content_hash(
            {"workspace_id": workspace_id, "body": body, "source_ids": requested_source_ids}
        )
        with self._locked() as payload:
            replay = self._replay_operation(
                payload,
                operation=f"create_note:{workspace_id}",
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
            if replay is not None:
                note = next(
                    (
                        item
                        for item in self._models(payload, "notes", ResearchNote)
                        if item.note_id == replay.get("note_id") and item.revision == 1
                    ),
                    None,
                )
                if note is None:
                    raise ResearchWorkspaceStoreError(
                        "note idempotency record references missing state"
                    )
                return note
            workspace = self._workspace(payload, workspace_id)
            if workspace is None:
                raise KeyError(workspace_id)
            current_source_ids = {
                source.source_id
                for source in self._models(payload, "sources", ResearchSource)
                if source.workspace_id == workspace_id
            }
            known_sources = {
                source.source_id
                for source_id in current_source_ids
                if (source := self._source(payload, source_id)) is not None
                and source.status == "active"
            }
            if not set(requested_source_ids).issubset(known_sources):
                raise ValueError("note references an unknown source")
            now = created_at or _now()
            note = ResearchNote(
                note_id=f"rn_{uuid4().hex[:20]}",
                workspace_id=workspace_id,
                owner_id=self.owner_id,
                body=body,
                source_ids=requested_source_ids,
                created_at=now,
                updated_at=now,
            )
            payload["notes"].append(note.model_dump(mode="json"))
            self._record_operation(
                payload,
                operation=f"create_note:{workspace_id}",
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                result={"note_id": note.note_id},
            )
            self._adapter.replace_all(payload)
            return note

    def list_notes(self, workspace_id: str) -> tuple[ResearchNote, ...]:
        return tuple(
            note
            for note in self._models(self._load(), "notes", ResearchNote)
            if note.workspace_id == workspace_id
        )


__all__ = [
    "ResearchRunLeaseUnavailable",
    "ResearchWorkspaceIdempotencyConflict",
    "ResearchWorkspaceStore",
    "ResearchWorkspaceStoreError",
    "ResearchWorkspaceVersionConflict",
]
