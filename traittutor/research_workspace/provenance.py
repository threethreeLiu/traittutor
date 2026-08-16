"""Minimal, replay-safe references from verified research to generation.

These models deliberately contain evidence *identity* only.  Report bodies,
claim text, source titles/URLs, prompts, credentials, and provider receipts
remain in their owner-bound systems and are never copied into a context
snapshot or an orchestration bundle.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PROVENANCE_VERSION: Literal["research-courseware.v1"] = "research-courseware.v1"


class ResearchCoursewareSourceRef(BaseModel):
    """One immutable active-source revision, without a browser locator."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str = Field(min_length=1, max_length=96)
    revision: int = Field(ge=1)


class ResearchCoursewareProvenance(BaseModel):
    """Typed, minimal evidence identity carried only by server composition.

    This is not a prompt payload.  It lets snapshot and prompt-bundle hashes
    distinguish a revised/revoked report without duplicating private evidence
    contents into independently persisted generation contracts.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal["research-courseware.v1"] = PROVENANCE_VERSION
    workspace_id: str = Field(min_length=1, max_length=96)
    research_run_id: str = Field(min_length=1, max_length=96)
    report_id: str = Field(min_length=1, max_length=96)
    report_revision: int = Field(ge=1)
    report_body_hash: str = Field(min_length=64, max_length=64)
    source_refs: tuple[ResearchCoursewareSourceRef, ...] = Field(min_length=1, max_length=200)
