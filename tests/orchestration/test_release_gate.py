from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path

import pytest

from traittutor.orchestration.release_gate import (
    AGENTIC_ACCEPTANCE_SCENARIOS,
    validate_agentic_acceptance_report,
)

ROOT = Path(__file__).resolve().parents[2]


def _write_report(path: Path, *, commit_sha: str, scenarios: set[str]) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": "courseware-agentic-acceptance-v1",
                "commit_sha": commit_sha,
                "validated_at": datetime.now(UTC).isoformat(),
                "passed": True,
                "scenarios": [
                    {"scenario_id": scenario_id, "passed": True}
                    for scenario_id in sorted(scenarios)
                ],
            }
        ),
        encoding="utf-8",
    )


def test_agentic_release_gate_requires_exact_scenarios_and_commit(tmp_path: Path) -> None:
    report_path = tmp_path / "agentic-report.json"
    commit_sha = "a" * 40
    _write_report(
        report_path,
        commit_sha=commit_sha,
        scenarios=set(AGENTIC_ACCEPTANCE_SCENARIOS),
    )

    report = validate_agentic_acceptance_report(report_path, commit_sha=commit_sha)
    assert report.passed is True

    with pytest.raises(ValueError, match="release commit"):
        validate_agentic_acceptance_report(report_path, commit_sha="b" * 40)

    _write_report(
        report_path,
        commit_sha=commit_sha,
        scenarios=set(AGENTIC_ACCEPTANCE_SCENARIOS) - {"planner_timeout"},
    )
    with pytest.raises(ValueError, match="scenarios mismatch"):
        validate_agentic_acceptance_report(report_path, commit_sha=commit_sha)


def test_production_installer_defaults_to_deterministic_and_deploy_requires_report() -> None:
    installer = (ROOT / "scripts" / "install_production_units.sh").read_text(encoding="utf-8")
    deploy = (ROOT / "scripts" / "deploy_production.sh").read_text(encoding="utf-8")

    assert 'COURSEWARE_MODE="${9:-deterministic}"' in installer
    assert "TRAITTUTOR_COURSEWARE_ORCHESTRATION_MODE=${COURSEWARE_MODE}" in installer
    assert "Environment=TRAITTUTOR_AUTH_ENABLED=true" in installer
    assert 'COURSEWARE_MODE="${TRAITTUTOR_DEPLOY_COURSEWARE_MODE:-deterministic}"' in deploy
    assert "agentic production mode requires TRAITTUTOR_AGENTIC_ACCEPTANCE_REPORT" in deploy
    assert "health ok: unauthenticated workspace redirect" in deploy
    assert "unauthenticated workspace redirect: healthy" in deploy
