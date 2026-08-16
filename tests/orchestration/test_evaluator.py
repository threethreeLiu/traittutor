from __future__ import annotations

import pytest

from traittutor.components import ComponentInstance, ComponentRegistry
from traittutor.generate.courseware import CoursewareArtifact, _lesson_schema
from traittutor.orchestration import (
    AgentTask,
    CoursewareEvaluator,
    CoursewareExecutor,
    CoursewarePromptBundle,
)


def test_evaluator_rejects_structured_external_claim_without_clickable_source() -> None:
    bundle = CoursewarePromptBundle(
        prompt_bundle_id="b",
        version="v1",
        context_snapshot_id="s",
        context_snapshot_hash="h",
        material_language="en",
        requested_component_types=("concept_explanation",),
        teaching_goal="teach",
        created_at="2026-08-09T00:00:00+00:00",
    )
    instance = ComponentInstance(
        instance_id="i",
        component_type="concept_explanation",
        version="v1",
        props={
            "body_markdown": "WHO reports that this is true.",
            "concept_refs": [
                {"claim": "WHO reports that this is true.", "source_url": "WHO report"}
            ],
        },
    )
    verdict = CoursewareEvaluator().evaluate(
        (instance,), bundle=bundle, registry=ComponentRegistry(), task_owners={"i": "t"}
    )
    assert verdict.status == "repair"
    assert verdict.offending_task_ids == ("t",)


def test_evaluator_accepts_structured_external_claim_with_clickable_source() -> None:
    bundle = CoursewarePromptBundle(
        prompt_bundle_id="b",
        version="v1",
        context_snapshot_id="s",
        context_snapshot_hash="h",
        material_language="en",
        requested_component_types=("concept_explanation",),
        teaching_goal="teach",
        created_at="2026-08-09T00:00:00+00:00",
    )
    instance = ComponentInstance(
        instance_id="i",
        component_type="concept_explanation",
        version="v1",
        props={
            "body_markdown": "WHO reports that this is true.",
            "concept_refs": [
                {
                    "claim": "WHO reports that this is true.",
                    "source_url": "https://www.who.int/example",
                }
            ],
        },
    )

    verdict = CoursewareEvaluator().evaluate(
        (instance,), bundle=bundle, registry=ComponentRegistry()
    )

    assert verdict.status == "passed"


def test_zh_content_with_ju_substring_is_not_false_flagged() -> None:
    # Regression for code-review finding #5: the bare CJK char "据" is a
    # substring of common zh words (根据/数据/证据) and would flag nearly all
    # zh courseware as an external claim. zh body text that contains "据" but
    # no structured external-claim record must pass.
    bundle = CoursewarePromptBundle(
        prompt_bundle_id="b",
        version="v1",
        context_snapshot_id="s",
        context_snapshot_hash="h",
        material_language="zh-CN",
        requested_component_types=("concept_explanation",),
        teaching_goal="teach",
        created_at="2026-08-09T00:00:00+00:00",
    )
    instance = ComponentInstance(
        instance_id="i",
        component_type="concept_explanation",
        version="v1",
        # "language" is intentionally omitted: concept_explanation does not allow
        # it (material_language already encodes the zh profile), and a None value
        # satisfies the language constraint check.
        props={"body_markdown": "根据已学知识，数据可以用证据说明。"},
    )
    verdict = CoursewareEvaluator().evaluate(
        (instance,), bundle=bundle, registry=ComponentRegistry()
    )
    assert verdict.status == "passed"


def test_courseware_contract_requires_claim_record_for_external_reference() -> None:
    section = {
        "section_title": "Evidence",
        "goal": "Inspect one claim",
        "core_content": "WHO reports the result.",
        "checkpoint": {
            "question": "What was reported?",
            "success_criteria": "Names the result",
            "feedback_if_confused": "Re-read the source.",
        },
        "reflection_prompt": "How strong is the evidence?",
        "references": ["external-web-1"],
        "external_claims": [],
    }
    lesson = {
        "title": "Evidence",
        "lesson_goal": "Evaluate claims",
        "sections": [section],
        "final_takeaways": [],
        "next_step_guidance": [],
    }

    with pytest.raises(ValueError, match="every referenced external chunk"):
        _lesson_schema(lesson, external_chunk_ids=frozenset({"external-web-1"}))


@pytest.mark.asyncio
async def test_courseware_executor_emits_clickable_external_claim_records() -> None:
    bundle = CoursewarePromptBundle(
        prompt_bundle_id="b",
        version="v1",
        context_snapshot_id="s",
        context_snapshot_hash="h",
        material_language="en",
        requested_component_types=("concept_explanation",),
        teaching_goal="teach",
        created_at="2026-08-09T00:00:00+00:00",
    )
    task = AgentTask(
        task_id="instruction",
        task_type="instruction",
        agent="courseware",
        depends_on=(),
        input_refs=(),
        produces_component_types=("concept_explanation",),
        budget_ms=1_000,
        timeout_ms=1_000,
    )
    chunks = [
        {
            "source_id": "web-source",
            "chunk_id": "external-web-1",
            "text": "WHO reports the result.",
            "source_url": "https://www.who.int/example",
        }
    ]

    async def body(**_kwargs: object) -> CoursewareArtifact:
        return CoursewareArtifact(
            lesson={
                "title": "Evidence",
                "sections": [
                    {
                        "section_title": "Evidence",
                        "core_content": "WHO reports the result.",
                        "references": ["external-web-1"],
                        "external_claims": [
                            {
                                "claim": "WHO reports the result.",
                                "source_chunk_id": "external-web-1",
                            }
                        ],
                    }
                ],
            },
            content_analysis={},
            adaptation_plan={},
            trace=[],
        )

    registry = ComponentRegistry()
    result = await CoursewareExecutor(
        payload_provider=lambda _task, _bundle: {"chunks": chunks},
        body=body,
    )(task, bundle, registry)

    component = result.produced_component_instances[0]
    assert component.props["concept_refs"] == [
        {
            "claim": "WHO reports the result.",
            "source_url": "https://www.who.int/example",
        }
    ]
    assert (
        CoursewareEvaluator()
        .evaluate(
            (component,),
            bundle=bundle,
            registry=registry,
        )
        .status
        == "passed"
    )
