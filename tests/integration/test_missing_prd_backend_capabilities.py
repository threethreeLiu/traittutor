"""Joint regression gates for the newly connected PRD backend capabilities.

These tests intentionally cross module boundaries.  Focused module suites own
individual state transitions; this file protects the product-level isolation,
non-resurrection, style-only, fencing, and public-contract invariants.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel
import pytest

from traittutor.api.routers.canonical_memory import (
    LongTermIndexEntryPublic,
    LongTermIndexStatusPublic,
    MemoryCandidatePublic,
    MemoryConflictPublic,
    MemoryGrantPublic,
    MemoryItemPublic,
    MemoryMutationPublic,
)
from traittutor.api.routers.research_workspace import (
    BriefPublic,
    ClaimPublic,
    NotePublic,
    ReportPublic,
    RunPublic,
    SourcePublic,
    WorkspacePublic,
)
from traittutor.api.routers.tutor_persona import TutorPersonaProfileResponse
from traittutor.learning_governance.models import (
    ErrorSummary,
    MisconceptionSummary,
    RepairSummary,
    ReviewSummary,
)
from traittutor.memory.api_models import MemoryMutationRequest, MemorySearchRequest
from traittutor.memory.index_store import MemoryIndexStore, StaleMemoryIndexGenerationError
from traittutor.memory.management import MemoryManagementService
from traittutor.memory.store import MemoryStore
from traittutor.research_workspace.executor import (
    ResearchClaimDraft,
    ResearchExecutionResult,
    ResearchSourceDraft,
)
from traittutor.research_workspace.models import ResearchRunStatus
from traittutor.research_workspace.service import ResearchWorkspaceService
from traittutor.research_workspace.store import ResearchWorkspaceStore
from traittutor.tutor_persona.compiler import TutorPersonaContract
from traittutor.tutor_persona.models import TutorPersonaSettings
from traittutor.tutor_persona.service import TutorPersonaService
from traittutor.tutor_persona.store import TutorPersonaStore

T0 = "2026-08-10T00:00:00+00:00"

_PUBLIC_RESPONSE_MODELS: tuple[type[BaseModel], ...] = (
    MemoryCandidatePublic,
    MemoryItemPublic,
    MemoryConflictPublic,
    MemoryGrantPublic,
    MemoryMutationPublic,
    LongTermIndexEntryPublic,
    LongTermIndexStatusPublic,
    ErrorSummary,
    MisconceptionSummary,
    RepairSummary,
    ReviewSummary,
    TutorPersonaProfileResponse,
    TutorPersonaContract,
    WorkspacePublic,
    BriefPublic,
    RunPublic,
    SourcePublic,
    NotePublic,
    ClaimPublic,
    ReportPublic,
)

_FORBIDDEN_PUBLIC_KEYS = {
    "owner_id",
    "user_id",
    "answer",
    "answers",
    "correct_answer",
    "expected_answer",
    "rubric",
    "rubric_ref",
    "correct_rule",
    "prompt",
    "raw_prompt",
    "system_prompt",
    "claim_token",
    "claimed_by",
    "lease_expires_at",
    "input_hash",
    "idempotency_key",
}


def test_owner_and_subject_partitions_hold_across_canonical_stores(tmp_path: Path) -> None:
    memory_path = tmp_path / "memory.json"
    alice_memory = MemoryStore("alice", path=memory_path)
    bob_memory = MemoryStore("bob", path=memory_path)
    alice_math = alice_memory.add_explicit(
        scope="subject",
        subject_id="math",
        kc_id="fractions",
        key="goal",
        value="master fractions",
    )
    alice_memory.add_explicit(
        scope="subject",
        subject_id="history",
        kc_id="renaissance",
        key="goal",
        value="compare primary sources",
    )
    bob_math = bob_memory.add_explicit(
        scope="subject",
        subject_id="math",
        kc_id="fractions",
        key="goal",
        value="review ratios",
    )

    alice_math_results = MemoryManagementService(
        "alice",
        store=alice_memory,
        index_store=MemoryIndexStore("alice", path=tmp_path / "alice-index.json"),
    ).search(MemorySearchRequest(scope="subject", subject_id="math"))
    bob_math_results = MemoryManagementService(
        "bob",
        store=bob_memory,
        index_store=MemoryIndexStore("bob", path=tmp_path / "bob-index.json"),
    ).search(MemorySearchRequest(scope="subject", subject_id="math"))

    assert [item.memory_id for item in alice_math_results] == [alice_math.memory_id]
    assert [item.memory_id for item in bob_math_results] == [bob_math.memory_id]
    assert all(item.subject_id == "math" for item in alice_math_results + bob_math_results)
    with pytest.raises(KeyError):
        bob_memory.item(alice_math.memory_id)

    research_path = tmp_path / "research.json"
    alice_research = ResearchWorkspaceStore("alice", path=research_path)
    bob_research = ResearchWorkspaceStore("bob", path=research_path)
    alice_workspace = alice_research.create_workspace(
        title="Alice math research",
        subject_id="math",
        idempotency_key="alice-math",
        created_at=T0,
    )
    bob_workspace = bob_research.create_workspace(
        title="Bob history research",
        subject_id="history",
        idempotency_key="bob-history",
        created_at=T0,
    )

    assert alice_research.list_workspaces() == (alice_workspace,)
    assert bob_research.list_workspaces() == (bob_workspace,)
    assert bob_research.get_workspace(alice_workspace.workspace_id) is None
    assert alice_research.get_workspace(bob_workspace.workspace_id) is None


def test_memory_delete_fences_late_index_and_fresh_rebuild_stays_empty(tmp_path: Path) -> None:
    service = MemoryManagementService(
        "alice",
        store=MemoryStore("alice", path=tmp_path / "memory.json"),
        index_store=MemoryIndexStore("alice", path=tmp_path / "index.json"),
    )
    item = service.store.add_explicit(
        scope="subject",
        subject_id="math",
        kc_id="fractions",
        key="private_note",
        value="do not resurrect",
    )

    initial_token = service.begin_index_rebuild()
    initial_index = service.build_memory_index(initial_token, entry_id="profile")
    service.commit_index_rebuild(initial_token, (initial_index,))
    late_token = service.begin_index_rebuild()
    late_index = service.build_memory_index(late_token, entry_id="profile")

    deleted = service.delete(
        item.memory_id,
        MemoryMutationRequest(operation_id="delete-private-note"),
    )

    assert deleted.item.status == "deleted"
    assert service.search(MemorySearchRequest(keyword="do not resurrect")) == []
    assert service.index_store.list_indexes() == []
    with pytest.raises(StaleMemoryIndexGenerationError):
        service.commit_index_rebuild(late_token, (late_index,))

    fresh_token = service.begin_index_rebuild()
    fresh_index = service.build_memory_index(fresh_token, entry_id="profile")
    service.commit_index_rebuild(fresh_token, (fresh_index,))
    assert fresh_index.claims == ()
    assert "do not resurrect" not in fresh_index.markdown
    assert all(
        item.memory_id not in claim.source_entry_ids
        for index in service.index_store.list_indexes()
        for claim in index.claims
    )


def test_persona_update_changes_style_attachment_only(tmp_path: Path) -> None:
    memory_path = tmp_path / "memory.json"
    research_path = tmp_path / "research.json"
    memory_store = MemoryStore("alice", path=memory_path)
    memory_store.add_explicit(
        scope="subject",
        subject_id="math",
        key="goal",
        value="learn calculus",
    )
    research_store = ResearchWorkspaceStore("alice", path=research_path)
    research_store.create_workspace(
        title="Calculus evidence",
        subject_id="math",
        idempotency_key="research-before-persona",
        created_at=T0,
    )
    memory_before = json.dumps(memory_store._load(), sort_keys=True)
    research_before = research_store._adapter.snapshot()  # noqa: SLF001

    service = TutorPersonaService(TutorPersonaStore("alice", path=tmp_path / "personas.json"))
    initial = service.get_profile()
    before = service.context(initial).model_dump(mode="json")
    changed = service.replace_profile(
        TutorPersonaSettings(
            tone="calm",
            feedback_format="socratic",
            voice_id="steady",
        ),
        expected_version=initial.version,
        idempotency_key="style-only-update",
    )
    after = service.context(changed).model_dump(mode="json")

    assert _changed_paths(before, after) == {
        "profile_ref",
        "contract_hash",
        "contract.profile_version",
        "contract.expression.tone",
        "contract.expression.feedback_format",
        "contract.modality.voice_id",
    }
    assert json.dumps(memory_store._load(), sort_keys=True) == memory_before
    assert research_store._adapter.snapshot() == research_before  # noqa: SLF001


@pytest.mark.parametrize(
    "transitions, terminal_status",
    [
        (("pausing", "paused"), "paused"),
        (("cancelling", "cancelled"), "cancelled"),
    ],
)
def test_pause_and_cancel_fence_late_research_results(
    tmp_path: Path,
    transitions: tuple[ResearchRunStatus, ...],
    terminal_status: ResearchRunStatus,
) -> None:
    service, run_id = _research_service_with_run(
        tmp_path / terminal_status,
        operation_suffix=terminal_status,
    )
    claimed = service.claim_run(
        run_id,
        worker_id="late-worker",
        lease_seconds=60,
        now=T0,
    )
    current = claimed
    for index, target in enumerate(transitions):
        current = service.transition_run(
            run_id,
            target,
            expected_revision=current.revision,
            idempotency_key=f"{terminal_status}-{index}",
        )

    receipt = service.commit_execution_result(
        claimed,
        task_id="late-report",
        result=_grounded_result(),
        created_at="2026-08-10T00:00:10+00:00",
    )

    assert receipt.outcome == "discarded_stale"
    persisted = service.get_run(run_id)
    assert persisted == current
    assert persisted is not None and persisted.status == terminal_status
    assert service.list_sources(current.workspace_id) == ()
    assert service.list_claims(run_id) == ()
    assert service.get_report(run_id) is None


def test_all_new_public_response_dtos_obey_sensitive_key_denylist() -> None:
    for model in _PUBLIC_RESPONSE_MODELS:
        property_names = _schema_property_names(model.model_json_schema())
        leaked = property_names & _FORBIDDEN_PUBLIC_KEYS
        assert not leaked, f"{model.__name__} exposes forbidden fields: {sorted(leaked)}"


def _research_service_with_run(
    root: Path,
    *,
    operation_suffix: str,
) -> tuple[ResearchWorkspaceService, str]:
    service = ResearchWorkspaceService(ResearchWorkspaceStore("alice", path=root / "research.json"))
    workspace = service.create_workspace(
        title="Fenced research",
        subject_id="math",
        idempotency_key=f"workspace-{operation_suffix}",
    )
    brief = service.save_brief(
        workspace.workspace_id,
        question="Can a late result change terminal state?",
        expected_workspace_revision=workspace.revision,
        idempotency_key=f"brief-{operation_suffix}",
    )
    run = service.start_run(
        workspace.workspace_id,
        brief_id=brief.brief_id,
        brief_version=brief.version,
        idempotency_key=f"run-{operation_suffix}",
    )
    return service, run.run_id


def _grounded_result() -> ResearchExecutionResult:
    return ResearchExecutionResult(
        sources=(
            ResearchSourceDraft(
                source_key="source",
                url="https://example.test/source",
                title="Late source",
                excerpt="This must not persist after fencing.",
            ),
        ),
        claims=(
            ResearchClaimDraft(
                claim_key="claim",
                text="A late grounded claim.",
                kind="grounded",
                source_keys=("source",),
            ),
        ),
        report_body="A late report.",
        report_claim_keys=("claim",),
    )


def _changed_paths(left: object, right: object, prefix: str = "") -> set[str]:
    if isinstance(left, dict) and isinstance(right, dict):
        changed: set[str] = set()
        for key in left.keys() | right.keys():
            path = f"{prefix}.{key}" if prefix else str(key)
            if key not in left or key not in right:
                changed.add(path)
            else:
                changed.update(_changed_paths(left[key], right[key], path))
        return changed
    return {prefix} if left != right else set()


def _schema_property_names(schema: dict[str, Any]) -> set[str]:
    names: set[str] = set()

    def visit(value: object) -> None:
        if isinstance(value, dict):
            properties = value.get("properties")
            if isinstance(properties, dict):
                names.update(str(key) for key in properties)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(schema)
    return names
