"""v2.7 ComponentSpec / PageSchema whitelist (F-08)."""

from __future__ import annotations

from .page_schema import ComponentInstance, PageRegion, PageSchema
from .page_store import PageStore, PageStoreError
from .projection import project_generation_result
from .registry import ComponentRegistry, get_default_registry
from .spec import (
    A11yRequirements,
    AnswerPolicy,
    ComponentExecutor,
    ComponentModality,
    ComponentSpec,
)
from .validation import (
    PageSchemaValidationError,
    safe_validate_or_degrade,
    text_degrade_page,
    validate_page_schema,
)

__all__ = [
    "A11yRequirements",
    "AnswerPolicy",
    "ComponentExecutor",
    "ComponentInstance",
    "ComponentModality",
    "ComponentRegistry",
    "ComponentSpec",
    "PageRegion",
    "PageSchema",
    "PageSchemaValidationError",
    "PageStore",
    "PageStoreError",
    "get_default_registry",
    "project_generation_result",
    "safe_validate_or_degrade",
    "text_degrade_page",
    "validate_page_schema",
]
