"""Retired GenerationResult-to-PageSchema boundary."""

from __future__ import annotations

from typing import Any, NoReturn

from .registry import ComponentRegistry


class GenerationResultProjectionRetiredError(ValueError):
    """Raised when a caller submits the removed GenerationResult shape."""


def project_generation_result(
    result: dict[str, Any],
    *,
    generation_run_id: str,
    registry: ComponentRegistry | None = None,
    created_at: str | None = None,
) -> NoReturn:
    """Reject the removed raw-result projection contract.

    PageSchema is created and persisted by the generation orchestrator. Keeping
    this fail-closed symbol temporarily prevents an indirect package import from
    breaking while ensuring no caller can revive the old projection path.
    """
    del result, generation_run_id, registry, created_at
    raise GenerationResultProjectionRetiredError(
        "GenerationResult projection is not supported; load the published PageSchema instead"
    )


__all__ = ["GenerationResultProjectionRetiredError", "project_generation_result"]
