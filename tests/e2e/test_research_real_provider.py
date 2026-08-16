"""Opt-in grounded Research Workspace smoke against the configured provider."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from traittutor.gateway.service import get_gateway
from traittutor.research_workspace.executor import (
    GatewayResearchExecutor,
    ResearchGatewayExecutionConfig,
    ResearchSourceDraft,
)
from traittutor.research_workspace.service import ResearchWorkspaceService
from traittutor.research_workspace.store import ResearchWorkspaceStore
from traittutor.research_workspace.worker import ResearchWorkspaceWorker


class _ValidatedSource:
    def sources_for(self, _task):
        return (
            ResearchSourceDraft(
                source_key="official",
                url="https://example.org/limits",
                title="Validated limits note",
                excerpt="A limit is the value a function approaches near a target.",
            ),
        )


@pytest.mark.asyncio
async def test_real_provider_completes_a_grounded_fenced_research_run(
    tmp_path: Path,
) -> None:
    if os.environ.get("TRAITTUTOR_RUN_REAL_PROVIDER_E2E") != "1":
        pytest.skip("set TRAITTUTOR_RUN_REAL_PROVIDER_E2E=1 to spend a provider call")

    store = ResearchWorkspaceStore("provider-smoke", path=tmp_path / "research.json")
    service = ResearchWorkspaceService(store)
    workspace = service.create_workspace(
        title="Provider smoke",
        subject_id="calculus",
        idempotency_key="workspace",
    )
    brief = service.save_brief(
        workspace.workspace_id,
        question="Explain the supplied definition of a limit in one sentence.",
        source_policy="web",
        expected_workspace_revision=workspace.revision,
        idempotency_key="brief",
    )
    run = service.start_run(
        workspace.workspace_id,
        brief_id=brief.brief_id,
        brief_version=brief.version,
        idempotency_key="run",
    )
    executor = GatewayResearchExecutor(
        get_gateway(),
        source_provider=_ValidatedSource(),  # type: ignore[arg-type]
        config=ResearchGatewayExecutionConfig(
            typed_messages=True,
            max_tokens=700,
            timeout_seconds=90,
        ),
        user_id="provider-smoke",
    )

    receipt = ResearchWorkspaceWorker(service, executor).run_once(
        run.run_id,
        worker_id="provider-smoke",
    )

    assert receipt is not None and receipt.outcome == "accepted", receipt
    completed = service.get_run(run.run_id)
    assert completed is not None and completed.status == "completed"
    report = service.get_report(run.run_id)
    assert report is not None and report.evidence_status == "active"
    claims = store.list_claims(run.run_id)
    assert claims and all(claim.source_ids for claim in claims if claim.kind == "grounded")
