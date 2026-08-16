"""Fail-closed production gate for F-07 real-provider acceptance."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

AGENTIC_ACCEPTANCE_SCENARIOS = frozenset(
    {
        "pure_material",
        "external_augmentation",
        "tool_failure",
        "planner_invalid_graph",
        "planner_timeout",
    }
)


class AgenticAcceptanceScenario(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scenario_id: str = Field(min_length=1, max_length=64)
    passed: Literal[True]


class AgenticAcceptanceReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["courseware-agentic-acceptance-v1"]
    commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    validated_at: str
    passed: Literal[True]
    scenarios: tuple[AgenticAcceptanceScenario, ...]

    @field_validator("validated_at")
    @classmethod
    def require_utc_timestamp(cls, value: str) -> str:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
            raise ValueError("validated_at must be UTC")
        return value


def validate_agentic_acceptance_report(
    path: Path,
    *,
    commit_sha: str,
) -> AgenticAcceptanceReport:
    """Require one passing real-provider result for every release scenario."""
    report = AgenticAcceptanceReport.model_validate_json(path.read_text(encoding="utf-8"))
    if report.commit_sha != commit_sha:
        raise ValueError("agentic acceptance report does not match the release commit")
    scenario_ids = [item.scenario_id for item in report.scenarios]
    if len(scenario_ids) != len(set(scenario_ids)):
        raise ValueError("agentic acceptance report contains duplicate scenarios")
    if set(scenario_ids) != AGENTIC_ACCEPTANCE_SCENARIOS:
        missing = sorted(AGENTIC_ACCEPTANCE_SCENARIOS.difference(scenario_ids))
        unexpected = sorted(set(scenario_ids).difference(AGENTIC_ACCEPTANCE_SCENARIOS))
        raise ValueError(
            f"agentic acceptance scenarios mismatch; missing={missing}, unexpected={unexpected}"
        )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    parser.add_argument("--commit-sha", required=True)
    args = parser.parse_args()
    validate_agentic_acceptance_report(args.report, commit_sha=args.commit_sha)
    print(json.dumps({"accepted": True, "commit_sha": args.commit_sha}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "AGENTIC_ACCEPTANCE_SCENARIOS",
    "AgenticAcceptanceReport",
    "AgenticAcceptanceScenario",
    "validate_agentic_acceptance_report",
]
