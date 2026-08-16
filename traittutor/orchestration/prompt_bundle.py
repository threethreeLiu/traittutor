"""Immutable prompt boundary for deterministic courseware orchestration."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from traittutor.research_workspace.provenance import ResearchCoursewareProvenance


def _require_utc_iso(value: str) -> str:
    """Keep replay and audit ordering unambiguous across worker time zones."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("created_at must be valid ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ValueError("created_at must include a UTC offset")
    return value


class CoursewarePromptBundle(BaseModel):
    """Frozen inputs used by every task in one courseware generation run.

    The bundle carries references rather than mutable learner state so injected
    executors cannot write Memory, BKT, ErrorRecord, ReviewItem, or Persona.
    A later context change must create a new bundle instead of changing the
    meaning of an already replayable run.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    prompt_bundle_id: str = Field(min_length=1, max_length=96)
    version: str = Field(min_length=1, max_length=16)
    context_snapshot_id: str
    context_snapshot_hash: str
    assistant_context_snapshot_id: str | None = None
    learner_profile_snapshot_id: str | None = None
    subject_state_snapshot_id: str | None = None
    bkt_model_version: str | None = None
    kc_mapping_version: str | None = None
    interaction_refs: tuple[str, ...] = ()
    persona_contract_ref: str | None = None
    grounding_refs: tuple[str, ...] = ()
    agent_roster_version: str = "courseware-agents-v1"
    component_catalog_version: str = "component-catalog-v1"
    quality_contract_version: str = "courseware-quality-v1"
    output_contract_version: str = "page-schema-v1"
    material_language: str
    requested_component_types: tuple[str, ...]
    teaching_goal: str = Field(min_length=1)
    created_at: str
    research_provenance: ResearchCoursewareProvenance | None = None

    _validate_created_at = field_validator("created_at")(_require_utc_iso)

    def model_post_init(self, __context: object) -> None:
        """Keep the historical snapshot field as the canonical compatibility seam."""
        del __context
        if self.assistant_context_snapshot_id is None:
            object.__setattr__(
                self,
                "assistant_context_snapshot_id",
                self.context_snapshot_id,
            )

    def task_input_refs(
        self,
        task_type: Literal[
            "material",
            "instruction",
            "practice",
            "srl",
            "visual",
            "ui_composer",
            "evaluator",
        ],
    ) -> tuple[str, ...]:
        """Return the least-context reference set for one specialist Agent.

        These are identities only. Executors receive the immutable bundle plus
        composition-root payload providers; no task gets raw events, full chat,
        private research prose, answer keys, or mutable learner stores.
        """
        bundle_ref = f"prompt_bundle:{self.prompt_bundle_id}"
        snapshot_ref = f"context_snapshot:{self.assistant_context_snapshot_id}"
        catalog_ref = f"component_catalog:{self.component_catalog_version}"
        quality_ref = f"quality_contract:{self.quality_contract_version}"
        output_ref = f"output_contract:{self.output_contract_version}"
        roster_ref = f"agent_roster:{self.agent_roster_version}"
        learner_refs = tuple(
            ref
            for ref in (
                (
                    f"learner_profile_snapshot:{self.learner_profile_snapshot_id}"
                    if self.learner_profile_snapshot_id
                    else None
                ),
                (
                    f"subject_state_snapshot:{self.subject_state_snapshot_id}"
                    if self.subject_state_snapshot_id
                    else None
                ),
                f"bkt_model:{self.bkt_model_version}" if self.bkt_model_version else None,
                f"kc_mapping:{self.kc_mapping_version}" if self.kc_mapping_version else None,
            )
            if ref is not None
        )
        grounding = tuple(f"grounding:{ref}" for ref in self.grounding_refs)
        interactions = tuple(f"interaction:{ref}" for ref in self.interaction_refs)

        refs_by_task = {
            "material": (bundle_ref, roster_ref, *grounding),
            "instruction": (
                bundle_ref,
                snapshot_ref,
                roster_ref,
                catalog_ref,
                *learner_refs,
                *grounding,
                *interactions,
                *(
                    (f"persona_contract:{self.persona_contract_ref}",)
                    if self.persona_contract_ref
                    else ()
                ),
            ),
            "practice": (
                bundle_ref,
                snapshot_ref,
                roster_ref,
                catalog_ref,
                quality_ref,
                *learner_refs,
                *grounding,
            ),
            "srl": (
                bundle_ref,
                snapshot_ref,
                roster_ref,
                catalog_ref,
                *learner_refs,
            ),
            "visual": (bundle_ref, roster_ref, catalog_ref, *grounding),
            # UI composition is intentionally blind to chat, learner state,
            # persona, and research notes. It sees only the trusted catalog and
            # the component references supplied by the orchestrator.
            "ui_composer": (bundle_ref, catalog_ref, output_ref),
            "evaluator": (bundle_ref, catalog_ref, quality_ref, output_ref, *grounding),
        }
        return tuple(dict.fromkeys(refs_by_task[task_type]))


def content_hash(bundle: CoursewarePromptBundle) -> str:
    """Hash generation-affecting content to make replay free and idempotent.

    A stable digest lets the orchestrator recognize the same work before it
    rebuilds components or invokes a billable executor. Serialization is
    canonical so tuple ordering remains meaningful while mapping and whitespace
    differences cannot create accidental duplicate runs (invariant #4).
    """
    canonical = json.dumps(
        {
            "bundle_version": bundle.version,
            "context_snapshot_id": bundle.context_snapshot_id,
            "context_snapshot_hash": bundle.context_snapshot_hash,
            "assistant_context_snapshot_id": bundle.assistant_context_snapshot_id,
            "learner_profile_snapshot_id": bundle.learner_profile_snapshot_id,
            "subject_state_snapshot_id": bundle.subject_state_snapshot_id,
            "bkt_model_version": bundle.bkt_model_version,
            "kc_mapping_version": bundle.kc_mapping_version,
            "interaction_refs": bundle.interaction_refs,
            "persona_contract_ref": bundle.persona_contract_ref,
            "grounding_refs": bundle.grounding_refs,
            "agent_roster_version": bundle.agent_roster_version,
            "component_catalog_version": bundle.component_catalog_version,
            "quality_contract_version": bundle.quality_contract_version,
            "output_contract_version": bundle.output_contract_version,
            "material_language": bundle.material_language,
            "requested_component_types": bundle.requested_component_types,
            "teaching_goal": bundle.teaching_goal,
            "research_provenance": (
                bundle.research_provenance.model_dump(mode="json")
                if bundle.research_provenance is not None
                else None
            ),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
