"""Registry of the registered learning components (F-08 whitelist).

The table migrates the existing ``learning_component_catalog.json`` types into
versioned ``ComponentSpec`` definitions. Specs are the only authority on which
props/actions a model may emit; anything not registered is rejected by the
validator with a text-degrade fallback.
"""

from __future__ import annotations

from typing import Any, Iterator, Literal, Mapping

from .spec import A11yRequirements, ComponentSpec

# Common props every teaching surface may use. Answer-bearing fields
# (answer, rubric, solution, back, key) are deliberately absent: they are held
# server-side and never appear in a public schema (invariant #5).
_COMMON_PROPS = (
    "title",
    "body_markdown",
    "concept_refs",
    "media_url",
    "a11y_label",
)

_PROPS: dict[str, tuple[str, ...]] = {
    "goal_map": (*_COMMON_PROPS, "milestones"),
    "concept_explanation": (*_COMMON_PROPS, "figure"),
    "worked_example": (*_COMMON_PROPS, "steps"),
    "visual_map": (*_COMMON_PROPS, "nodes", "edges"),
    "video_explanation": _COMMON_PROPS,
    "audio_explanation": _COMMON_PROPS,
    "diagnostic_check": (*_COMMON_PROPS, "prompt", "stimulus"),
    "guided_practice": (*_COMMON_PROPS, "prompt", "stimulus", "hint"),
    "calibration_checkpoint": (*_COMMON_PROPS, "prompt"),
    "retrieval_card": (*_COMMON_PROPS, "front", "hint"),
    "progress_checkpoint": _COMMON_PROPS,
    "reflection_prompt": (*_COMMON_PROPS, "prompt"),
    "transfer_challenge": (*_COMMON_PROPS, "prompt", "stimulus"),
    "review_queue": (*_COMMON_PROPS, "item_refs"),
}

_INTERACTIVE_ACTIONS = ("submit", "reveal_hint", "skip")
_STATIC_ACTIONS = ("acknowledge", "next", "replay")

_EXECUTOR_MODALITY: dict[str, str] = {
    "deterministic": "text",
    "lesson": "text",
    "retrieval": "interactive",
    "assessment": "interactive",
    "image": "visual",
    "video": "video",
    "audio": "audio",
}


def _build_specs(catalog: Mapping[str, Any]) -> dict[str, ComponentSpec]:
    components: Mapping[str, Mapping[str, Any]] = catalog["components"]
    specs: dict[str, ComponentSpec] = {}
    for component_type, definition in components.items():
        executor = str(definition["executor"])
        answer_policy: Literal["server_held", "none"] = (
            "server_held" if executor in {"assessment", "retrieval"} else "none"
        )
        actions = (
            _INTERACTIVE_ACTIONS if executor in {"assessment", "retrieval"} else _STATIC_ACTIONS
        )
        specs[component_type] = ComponentSpec(
            component_type=component_type,
            version="v1",
            executor=executor,  # type: ignore[arg-type]
            modality=_EXECUTOR_MODALITY[executor],  # type: ignore[arg-type]
            completion_event=str(definition["completion_event"]),
            answer_policy=answer_policy,
            degrades_to_text=True,
            allowed_props=_PROPS[component_type],
            allowed_actions=actions,
            a11y=A11yRequirements(
                requires_label=True,
                keyboard_operable=True,
                text_alternative_required=executor in {"image", "video", "audio"},
            ),
            label_zh=str(definition["label_zh"]),
            label_en=str(definition["label_en"]),
        )
    return specs


class ComponentRegistry:
    """Immutable lookup of registered component specs."""

    def __init__(self, specs: Mapping[str, ComponentSpec] | None = None) -> None:
        if specs is None:
            from traittutor.learning_components import load_learning_component_catalog

            specs = _build_specs(load_learning_component_catalog())
        self._specs: dict[str, ComponentSpec] = dict(specs)

    def is_registered(self, component_type: str) -> bool:
        return component_type in self._specs

    def get(self, component_type: str) -> ComponentSpec | None:
        return self._specs.get(component_type)

    def require(self, component_type: str) -> ComponentSpec:
        spec = self._specs.get(component_type)
        if spec is None:
            raise KeyError(f"unregistered component type: {component_type}")
        return spec

    def __iter__(self) -> Iterator[ComponentSpec]:
        return iter(self._specs.values())

    def __len__(self) -> int:
        return len(self._specs)


_DEFAULT_REGISTRY: ComponentRegistry | None = None


def get_default_registry() -> ComponentRegistry:
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = ComponentRegistry()
    return _DEFAULT_REGISTRY
