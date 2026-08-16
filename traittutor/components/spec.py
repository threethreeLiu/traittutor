"""Registered component specifications for the v2.7 PageSchema whitelist.

A ``ComponentSpec`` is the authoritative, server-owned definition of one
teachable component: which props a model may fill, which actions the surface
may emit, whether the answer is held server-side, and whether it can degrade
to a text page. The model never authors a spec; it only fills a registered
one (PRD §6.6 / F-08, invariant #8).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ComponentExecutor = Literal[
    "deterministic", "lesson", "retrieval", "assessment", "image", "video", "audio"
]
ComponentModality = Literal["text", "interactive", "visual", "video", "audio"]
AnswerPolicy = Literal["server_held", "none"]


class A11yRequirements(BaseModel):
    """Baseline accessibility contract every registered component must meet."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    requires_label: bool = True
    keyboard_operable: bool = True
    text_alternative_required: bool = False


class ComponentSpec(BaseModel):
    """Immutable definition of one registered learning component type."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    component_type: str = Field(min_length=1, max_length=64)
    version: str = Field(min_length=1, max_length=16)
    executor: ComponentExecutor
    modality: ComponentModality
    completion_event: str = Field(min_length=1, max_length=64)
    answer_policy: AnswerPolicy
    degrades_to_text: bool = True
    allowed_props: tuple[str, ...] = Field(min_length=1)
    allowed_actions: tuple[str, ...] = Field(min_length=1)
    a11y: A11yRequirements = Field(default_factory=A11yRequirements)
    label_zh: str = Field(min_length=1, max_length=64)
    label_en: str = Field(min_length=1, max_length=64)

    def allows_prop(self, key: str) -> bool:
        return key in self.allowed_props

    def allows_action(self, action: str) -> bool:
        return action in self.allowed_actions
