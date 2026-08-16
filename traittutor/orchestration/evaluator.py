"""Deterministic release evaluator for generated component sets."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from typing import Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, ValidationError

from traittutor.components import ComponentInstance, ComponentRegistry, PageRegion, PageSchema
from traittutor.components.validation import PageSchemaValidationError, validate_page_schema

from .prompt_bundle import CoursewarePromptBundle


class EvaluatorVerdict(BaseModel):
    """Persistable evaluator closure, including directed repair notes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["passed", "repair", "degraded", "failed"]
    findings: tuple[str, ...] = ()
    offending_task_ids: tuple[str, ...] = ()
    repair_note: str = ""


class ExternalClaimRecord(BaseModel):
    """Explicit attribution for an externally sourced generated claim.

    ``concept_refs`` also carries ordinary KC and material-chunk references, so
    strings in that collection cannot prove external attribution.  An external
    claim must instead opt into this closed record shape and provide a URL that
    a learner can actually open (invariant #7).
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    claim: str = Field(min_length=1, max_length=2_000)
    source_url: AnyHttpUrl


class CoursewareEvaluator:
    """Apply the F-07 release checks without an LLM or learner-state writes."""

    @staticmethod
    def _external_claim_findings(instance: ComponentInstance) -> tuple[str, ...]:
        """Validate explicit claim records without guessing from prose keywords."""
        refs = instance.props.get("concept_refs", ())
        if not isinstance(refs, (list, tuple)):
            return ()

        findings: list[str] = []
        for index, ref in enumerate(refs):
            if not isinstance(ref, Mapping):
                continue
            # Other structured refs (for example material source/chunk/snippet)
            # are not external claims. Presence of either claim-specific key
            # opts the record into this fail-closed contract.
            if "claim" not in ref and "source_url" not in ref:
                continue
            try:
                ExternalClaimRecord.model_validate(dict(ref))
            except ValidationError:
                findings.append(
                    f"{instance.instance_id}: external claim record {index} "
                    "requires a claim and clickable http(s) source_url"
                )
        return tuple(findings)

    def evaluate(
        self,
        components: tuple[ComponentInstance, ...],
        *,
        bundle: CoursewarePromptBundle,
        registry: ComponentRegistry,
        task_owners: dict[str, str] | None = None,
    ) -> EvaluatorVerdict:
        findings: list[str] = []
        offending: set[str] = set()
        owners = task_owners or {}
        concept_versions: dict[str, set[str]] = defaultdict(set)

        for instance in components:
            owner = owners.get(instance.instance_id, instance.instance_id)
            spec = registry.get(instance.component_type)
            if spec is None:
                findings.append(f"{instance.instance_id}: unregistered component type")
                offending.add(owner)
                continue
            claim_findings = self._external_claim_findings(instance)
            if claim_findings:
                findings.extend(claim_findings)
                offending.add(owner)
            refs = instance.props.get("concept_refs", ())
            if isinstance(refs, (list, tuple)):
                for ref in refs:
                    if isinstance(ref, Mapping):
                        continue
                    text = str(ref)
                    concept, separator, version = text.partition("@")
                    if separator:
                        concept_versions[concept].add(version)
            if bundle.material_language.lower().startswith("zh") and instance.props.get(
                "language"
            ) not in (None, "zh", "zh-CN", "zh-Hans"):
                findings.append(f"{instance.instance_id}: profile language constraint violated")
                offending.add(owner)

        for concept, versions in sorted(concept_versions.items()):
            if len(versions) > 1:
                findings.append(
                    f"concept {concept} has inconsistent versions: {', '.join(sorted(versions))}"
                )
                for instance in components:
                    if any(
                        str(ref).startswith(f"{concept}@")
                        for ref in instance.props.get("concept_refs", ())
                    ):
                        offending.add(owners.get(instance.instance_id, instance.instance_id))

        try:
            validate_page_schema(
                PageSchema(
                    page_schema_id="evaluation-candidate",
                    generation_run_id="evaluation",
                    version="v1",
                    regions=[
                        PageRegion(region_id=f"evaluation-{index}", component=component)
                        for index, component in enumerate(components)
                    ]
                    or [PageRegion(region_id="evaluation-empty")],
                    published=False,
                    created_at=bundle.created_at,
                ),
                registry=registry,
            )
        except (PageSchemaValidationError, ValueError) as exc:
            findings.append(f"component schema/safety violation: {exc}")
            offending.update(owners.values())

        if not findings:
            return EvaluatorVerdict(status="passed")
        note = "; ".join(findings)
        return EvaluatorVerdict(
            status="repair",
            findings=tuple(findings),
            offending_task_ids=tuple(sorted(offending)),
            repair_note=note,
        )


__all__ = ["CoursewareEvaluator", "EvaluatorVerdict", "ExternalClaimRecord"]
