"""F-08 acceptance: the PageSchema whitelist rejects everything it must."""

from __future__ import annotations

import pytest

from traittutor.components import (
    ComponentInstance,
    ComponentRegistry,
    PageRegion,
    PageSchema,
    PageSchemaValidationError,
    safe_validate_or_degrade,
    text_degrade_page,
    validate_page_schema,
)

CREATED = "2026-08-09T08:00:00+00:00"
_ALL_TYPES = (
    "goal_map",
    "concept_explanation",
    "worked_example",
    "visual_map",
    "video_explanation",
    "audio_explanation",
    "diagnostic_check",
    "guided_practice",
    "calibration_checkpoint",
    "retrieval_card",
    "progress_checkpoint",
    "reflection_prompt",
    "transfer_challenge",
    "review_queue",
)


def _page(
    *,
    component_type: str = "concept_explanation",
    props: dict[str, object] | None = None,
    page_schema_id: str = "p1",
    supersedes_page_id: str | None = None,
) -> PageSchema:
    instance = ComponentInstance(
        instance_id="i1",
        component_type=component_type,
        version="v1",
        props=props if props is not None else {"title": "T", "body_markdown": "B"},
    )
    return PageSchema(
        page_schema_id=page_schema_id,
        generation_run_id="run1",
        version="v1",
        regions=[PageRegion(region_id="r1", component=instance)],
        supersedes_page_id=supersedes_page_id,
        created_at=CREATED,
    )


def test_registry_registers_all_learning_component_types() -> None:
    reg = ComponentRegistry()
    assert len(reg) == 14
    assert all(reg.is_registered(t) for t in _ALL_TYPES)


def test_assessment_components_hold_answers_server_side() -> None:
    reg = ComponentRegistry()
    assert reg.require("diagnostic_check").answer_policy == "server_held"
    assert reg.require("retrieval_card").answer_policy == "server_held"
    assert reg.require("concept_explanation").answer_policy == "none"


def test_valid_page_passes() -> None:
    validate_page_schema(_page())


def test_unregistered_component_rejected() -> None:
    with pytest.raises(PageSchemaValidationError):
        validate_page_schema(_page(component_type="malicious_widget"))


def test_unknown_field_rejected() -> None:
    with pytest.raises(PageSchemaValidationError):
        validate_page_schema(_page(props={"title": "T", "answer": "leaked"}))


def test_version_mismatch_rejected() -> None:
    instance = ComponentInstance(
        instance_id="i1", component_type="concept_explanation", version="v9", props={"title": "T"}
    )
    page = PageSchema(
        page_schema_id="p1",
        generation_run_id="r",
        version="v1",
        regions=[PageRegion(region_id="r1", component=instance)],
        created_at=CREATED,
    )
    with pytest.raises(PageSchemaValidationError):
        validate_page_schema(page)


def test_answer_leak_field_rejected() -> None:
    with pytest.raises(PageSchemaValidationError):
        validate_page_schema(_page(props={"title": "T", "rubric": "scoring"}))


def test_nested_answer_leak_rejected() -> None:
    # A leak buried inside a nested prop must still be caught.
    with pytest.raises(PageSchemaValidationError):
        validate_page_schema(_page(props={"title": "T", "steps": [{"solution": "hidden"}]}))


def test_executable_content_rejected() -> None:
    with pytest.raises(PageSchemaValidationError):
        validate_page_schema(
            _page(props={"title": "T", "body_markdown": "<script>alert(1)</script>"})
        )


def test_media_url_with_script_scheme_rejected() -> None:
    with pytest.raises(PageSchemaValidationError):
        validate_page_schema(_page(props={"title": "T", "media_url": "javascript:alert(1)"}))


def test_media_url_svg_data_uri_rejected() -> None:
    # SVG can carry <script>/onload and escapes the plaintext marker scan when
    # base64-encoded; it must be refused in any form.
    with pytest.raises(PageSchemaValidationError):
        validate_page_schema(
            _page(props={"title": "T", "media_url": "data:image/svg+xml;base64,PHN2Zz4="})
        )


def test_media_url_svg_remote_rejected() -> None:
    with pytest.raises(PageSchemaValidationError):
        validate_page_schema(
            _page(props={"title": "T", "media_url": "https://cdn.example.com/diagram.svg"})
        )


def test_executable_markers_catch_extra_vectors() -> None:
    # Defense-in-depth: vbscript:, CSS expression()/@import, and extra tag
    # names must be rejected even inside an otherwise-allowed string prop.
    for payload in (
        "x vbscript:msgbox(1)",
        "style=expression(alert(1))",
        "@import url(evil.css)",
        "<object data=x>",
        "<embed src=x>",
        "<style>.x{}</style>",
        "<form action=x></form>",
    ):
        with pytest.raises(PageSchemaValidationError):
            validate_page_schema(_page(props={"title": "T", "body_markdown": payload}))


def test_text_degrade_page_is_valid_and_completable() -> None:
    degraded = text_degrade_page(
        page_schema_id="p1", generation_run_id="r", reason="bad", created_at=CREATED
    )
    validate_page_schema(degraded)  # the degrade page itself passes the gate
    assert degraded.supersedes_page_id == "p1"


def test_safe_validate_or_degrade_returns_degrade_on_failure() -> None:
    bad = _page(component_type="malicious_widget")
    out = safe_validate_or_degrade(bad, generation_run_id="r", created_at=CREATED)
    assert out.page_schema_id != bad.page_schema_id
    validate_page_schema(out)


def test_no_self_supersede() -> None:
    with pytest.raises(PageSchemaValidationError):
        validate_page_schema(_page(page_schema_id="p1", supersedes_page_id="p1"))


def test_duplicate_instance_id_rejected() -> None:
    instance = ComponentInstance(
        instance_id="dup", component_type="concept_explanation", version="v1", props={"title": "T"}
    )
    page = PageSchema(
        page_schema_id="p1",
        generation_run_id="r",
        version="v1",
        regions=[
            PageRegion(region_id="r1", component=instance),
            PageRegion(region_id="r2", component=instance),
        ],
        created_at=CREATED,
    )
    with pytest.raises(PageSchemaValidationError):
        validate_page_schema(page)
