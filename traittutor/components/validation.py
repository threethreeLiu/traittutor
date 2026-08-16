"""Validation + safe text-degrade for PageSchema content (F-08 release gate).

Enforces the whitelist (invariant #8): only registered components, only their
allowed props/actions, compatible versions, no answer leakage (invariant #5),
and no executable/remote content. Any violation rejects the page and returns a
deterministic text-degrade page that still completes the core task.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import ValidationError

from .page_schema import ComponentInstance, PageRegion, PageSchema
from .registry import ComponentRegistry, get_default_registry

# Field names that would leak a server-held answer into a public schema.
_ANSWER_LEAK_KEYS = re.compile(
    r"^(answer|answers|rubric|solution|solutions|key|keys|back|correct|expected)$"
)
# Executable / remote-content markers that must never appear in a string value.
# Defense-in-depth on top of the component prop whitelist: even if a value
# slips into an allowed string prop, these markers reject active content.
_EXECUTABLE_MARKERS = re.compile(
    r"<\s*script|<\s*iframe|<\s*object|<\s*embed|<\s*style|<\s*link|<\s*form"
    r"|<\s*base|<\s*meta|javascript:|vbscript:|data:text/html"
    r"|on[a-z]+\s*=|expression\s*\(|@import",
    re.IGNORECASE,
)
_REMOTE_URL = re.compile(r"^https?://", re.IGNORECASE)


def _looks_like_svg(value: str) -> bool:
    """SVG can carry ``<script>``/event handlers and, once base64-encoded as a
    data URI, escapes the plaintext marker scan above — refuse it in any form."""
    lowered = value.lower()
    bare = lowered.split("#", 1)[0].split("?", 1)[0].rstrip("/")
    return "image/svg" in lowered or bare.endswith(".svg")


class PageSchemaValidationError(ValueError):
    """Raised when a page schema violates the whitelist or safety rules."""


def _scan_value(path: str, value: Any) -> None:
    if isinstance(value, str):
        if _EXECUTABLE_MARKERS.search(value):
            raise PageSchemaValidationError(f"executable content forbidden at {path}")
    elif isinstance(value, dict):
        for key, child in value.items():
            if isinstance(key, str) and _ANSWER_LEAK_KEYS.match(key):
                raise PageSchemaValidationError(f"answer-held field '{key}' forbidden at {path}")
            _scan_value(f"{path}.{key}", child)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _scan_value(f"{path}[{index}]", child)


def _is_allowed_media_url(value: str) -> bool:
    # Reject anything that smuggles an executable scheme or active content;
    # refuse SVG (script-bearing) in any form; permit plain http(s) media,
    # same-origin paths, and non-SVG image data URIs. A deployment can further
    # restrict http(s) media to an approved-host allowlist.
    if _EXECUTABLE_MARKERS.search(value) or _looks_like_svg(value):
        return False
    return bool(_REMOTE_URL.match(value)) or value.startswith(("/", "data:image/"))


def _validate_instance(instance: ComponentInstance, registry: ComponentRegistry) -> None:
    spec = registry.get(instance.component_type)
    if spec is None:
        raise PageSchemaValidationError(f"unregistered component type: {instance.component_type}")
    if instance.version != spec.version:
        raise PageSchemaValidationError(
            f"version mismatch for {instance.component_type}: "
            f"page={instance.version} registry={spec.version}"
        )
    for key in instance.props:
        if not spec.allows_prop(key):
            raise PageSchemaValidationError(f"unknown field '{key}' for {instance.component_type}")
        if key == "media_url":
            value = instance.props[key]
            if isinstance(value, str) and not _is_allowed_media_url(value):
                raise PageSchemaValidationError(f"disallowed media_url at {instance.instance_id}")
    _scan_value(f"props:{instance.instance_id}", instance.props)


def validate_page_schema(
    schema: PageSchema, *, registry: ComponentRegistry | None = None
) -> PageSchema:
    """Validate a page against the registry; raise on any violation."""
    if schema.supersedes_page_id == schema.page_schema_id:
        raise PageSchemaValidationError("a page cannot supersede itself")
    reg = registry or get_default_registry()
    seen: set[str] = set()
    for region in schema.regions:
        if region.component is not None:
            if region.component.instance_id in seen:
                raise PageSchemaValidationError(
                    f"duplicate instance_id: {region.component.instance_id}"
                )
            seen.add(region.component.instance_id)
            _validate_instance(region.component, reg)
    return schema


def text_degrade_page(
    *,
    page_schema_id: str,
    generation_run_id: str,
    reason: str,
    created_at: str,
) -> PageSchema:
    """Build the deterministic text-degrade fallback page.

    Used when a generated page fails validation or a media dependency fails.
    The page keeps the core task completable with text only (F-08 acceptance).
    """
    body = (
        "This content could not be shown safely, so it has been replaced with a "
        f"text version. ({reason}) You can still read, answer, and proceed."
    )
    instance = ComponentInstance(
        instance_id=f"{page_schema_id}:degrade",
        component_type="concept_explanation",
        version="v1",
        props={
            "title": "Text-only version",
            "body_markdown": body,
            "a11y_label": "Text-only version",
        },
    )
    return PageSchema(
        page_schema_id=f"{page_schema_id}:degrade",
        generation_run_id=generation_run_id,
        version="v1",
        regions=[PageRegion(region_id="degrade", component=instance, heading="Text-only version")],
        supersedes_page_id=page_schema_id,
        published=False,
        created_at=created_at,
    )


def safe_validate_or_degrade(
    schema: PageSchema,
    *,
    generation_run_id: str,
    created_at: str,
    reason_prefix: str = "validation_failed",
    registry: ComponentRegistry | None = None,
) -> PageSchema:
    """Validate; on any failure return a text-degrade page instead of raising."""
    try:
        validate_page_schema(schema, registry=registry)
        return schema
    except (PageSchemaValidationError, ValidationError, ValueError) as exc:
        return text_degrade_page(
            page_schema_id=schema.page_schema_id,
            generation_run_id=generation_run_id,
            reason=f"{reason_prefix}:{exc}",
            created_at=created_at,
        )
