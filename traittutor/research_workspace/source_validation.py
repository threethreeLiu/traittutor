"""Evidence validation gates for durable research artifacts."""

from __future__ import annotations

from collections.abc import Iterable

from .models import ResearchClaim, ResearchReportArtifact, ResearchSource


class ResearchEvidenceValidationError(ValueError):
    """Research evidence is inconsistent or cannot support its claims."""


def validate_sources_and_claims(
    *,
    sources: Iterable[ResearchSource],
    claims: Iterable[ResearchClaim],
    workspace_id: str,
    run_id: str,
    owner_id: str,
    existing_source_ids: Iterable[str] = (),
) -> None:
    """Require owner/workspace/run alignment and clickable grounded evidence."""

    source_list = tuple(sources)
    claim_list = tuple(claims)
    source_ids = set(existing_source_ids)
    for source in source_list:
        if source.owner_id != owner_id or source.workspace_id != workspace_id:
            raise ResearchEvidenceValidationError("source is outside the run partition")
        if source.source_id in source_ids:
            raise ResearchEvidenceValidationError("source IDs must be unique")
        if source.url.scheme not in {"http", "https"}:
            raise ResearchEvidenceValidationError("sources must use HTTP(S) URLs")
        source_ids.add(source.source_id)

    claim_ids: set[str] = set()
    for claim in claim_list:
        if (
            claim.owner_id != owner_id
            or claim.workspace_id != workspace_id
            or claim.run_id != run_id
        ):
            raise ResearchEvidenceValidationError("claim is outside the run partition")
        if claim.claim_id in claim_ids:
            raise ResearchEvidenceValidationError("claim IDs must be unique")
        claim_ids.add(claim.claim_id)
        if claim.kind == "grounded" and not set(claim.source_ids).issubset(source_ids):
            raise ResearchEvidenceValidationError("grounded claim references an unknown source")


def validate_report(
    report: ResearchReportArtifact,
    *,
    workspace_id: str,
    run_id: str,
    owner_id: str,
    claim_ids: Iterable[str],
) -> None:
    """Require a report to reference only claims committed in its partition."""

    if (
        report.owner_id != owner_id
        or report.workspace_id != workspace_id
        or report.run_id != run_id
    ):
        raise ResearchEvidenceValidationError("report is outside the run partition")
    if not set(report.claim_ids).issubset(set(claim_ids)):
        raise ResearchEvidenceValidationError("report references an unknown claim")


__all__ = [
    "ResearchEvidenceValidationError",
    "validate_report",
    "validate_sources_and_claims",
]
