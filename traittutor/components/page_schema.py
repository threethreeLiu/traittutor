"""Frozen, code-free page schemas rendered from registered components.

A ``PageSchema`` is a layout of component instances plus data bindings. It
intentionally carries no HTML, JS, CSS, remote component URLs, answer keys,
or rubrics — answers stay server-side (invariant #5) and the model may only
fill registered components (invariant #8). A page that has been published and
interacted with is immutable; new state produces a new page via
``supersedes_page_id`` (invariant #11).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .spec import ComponentModality


class ComponentInstance(BaseModel):
    """One bound component: a registered type/version plus validated props.

    Props are opaque here; ``validation.validate_page_schema`` enforces the
    whitelist against the registry and rejects answer leakage and executable
    content. Carrying an answer here would violate invariant #5.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    instance_id: str = Field(min_length=1, max_length=96)
    component_type: str = Field(min_length=1, max_length=64)
    version: str = Field(min_length=1, max_length=16)
    props: dict[str, Any] = Field(default_factory=dict)
    modality_hint: ComponentModality | None = None


class PageRegion(BaseModel):
    """A single layout slot that may bind one component instance."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    region_id: str = Field(min_length=1, max_length=96)
    component: ComponentInstance | None = None
    heading: str | None = Field(default=None, max_length=160)


class PageSchema(BaseModel):
    """Immutable layout bound for one rendered assistant page."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    page_schema_id: str = Field(min_length=1, max_length=96)
    generation_run_id: str = Field(min_length=1, max_length=96)
    version: str = Field(min_length=1, max_length=16)
    regions: list[PageRegion] = Field(min_length=1, max_length=24)
    supersedes_page_id: str | None = None
    published: bool = False
    created_at: str
