"""Public request contracts and config validators for MVP capabilities."""

from __future__ import annotations

from typing import Any, Callable, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from traittutor.agents.research.request_config import (
    DeepResearchRequestConfig,
    validate_research_request_config,
)

_RUNTIME_ONLY_KEYS = {
    "_persist_user_message",
    "followup_question_context",
    # TraitTutor chat composer shortcut (courseware / flashcards / quiz /
    # exploration / diagrams). The chat runtime consumes it after public
    # capability validation, so it is routing metadata rather than public
    # chat-config surface area.
    "traittutor_mode",
}


class ChatRequestConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MasteryPathRequestConfig(BaseModel):
    """An explicit existing path choice; its subject is always server-derived."""

    model_config = ConfigDict(extra="forbid")

    learning_path_id: str | None = Field(default=None, min_length=1, max_length=160)


class VisualizeRequestConfig(BaseModel):
    """Public, fail-closed configuration accepted by the visualize capability."""

    model_config = ConfigDict(extra="forbid")

    render_mode: Literal[
        "auto",
        "svg",
        "chartjs",
        "mermaid",
        "html",
        "manim_video",
        "manim_image",
    ] = "auto"
    quality: Literal["low", "medium", "high"] = "medium"
    style_hint: str = Field(default="", max_length=500)


_RequestConfigT = TypeVar("_RequestConfigT", bound=BaseModel)


def _clean_public_config(raw_config: dict[str, Any] | None) -> dict[str, Any]:
    if raw_config is None:
        return {}
    if not isinstance(raw_config, dict):
        raise ValueError("Capability config must be an object.")
    cleaned = dict(raw_config)
    for key in _RUNTIME_ONLY_KEYS:
        cleaned.pop(key, None)
    return cleaned


def _validate_model(
    model_type: type[_RequestConfigT],
    raw_config: dict[str, Any] | None,
    *,
    label: str,
) -> _RequestConfigT:
    cleaned = _clean_public_config(raw_config)
    try:
        return model_type.model_validate(cleaned)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        )
        raise ValueError(f"Invalid {label} config: {details}") from exc


def validate_chat_request_config(raw_config: dict[str, Any] | None) -> ChatRequestConfig:
    return _validate_model(ChatRequestConfig, raw_config, label="chat")


def validate_mastery_path_request_config(
    raw_config: dict[str, Any] | None,
) -> MasteryPathRequestConfig:
    return _validate_model(MasteryPathRequestConfig, raw_config, label="mastery path")


def validate_visualize_request_config(
    raw_config: dict[str, Any] | None,
) -> VisualizeRequestConfig:
    return _validate_model(VisualizeRequestConfig, raw_config, label="visualize")


def build_request_schema(model_type: type[BaseModel]) -> dict[str, Any]:
    return model_type.model_json_schema(mode="validation")


CAPABILITY_CONFIG_VALIDATORS: dict[str, Callable[[dict[str, Any] | None], Any]] = {
    "chat": validate_chat_request_config,
    "deep_solve": validate_chat_request_config,
    "learning_exploration": validate_chat_request_config,
    "knowledge_diagram": validate_chat_request_config,
    "humanizer": validate_chat_request_config,
    "mastery_path": validate_mastery_path_request_config,
    "deep_research": validate_research_request_config,
    "visualize": validate_visualize_request_config,
}

CAPABILITY_REQUEST_SCHEMAS: dict[str, dict[str, Any]] = {
    "chat": build_request_schema(ChatRequestConfig),
    "deep_solve": build_request_schema(ChatRequestConfig),
    "learning_exploration": build_request_schema(ChatRequestConfig),
    "knowledge_diagram": build_request_schema(ChatRequestConfig),
    "humanizer": build_request_schema(ChatRequestConfig),
    "mastery_path": build_request_schema(MasteryPathRequestConfig),
    "deep_research": build_request_schema(DeepResearchRequestConfig),
    "visualize": build_request_schema(VisualizeRequestConfig),
}


def validate_capability_config(
    capability: str, raw_config: dict[str, Any] | None
) -> dict[str, Any]:
    validator = CAPABILITY_CONFIG_VALIDATORS.get(capability)
    if validator is None:
        return _clean_public_config(raw_config)
    model = validator(raw_config)
    if isinstance(model, BaseModel):
        return model.model_dump(exclude_none=True)
    return _clean_public_config(raw_config)


def get_capability_request_schema(capability: str) -> dict[str, Any]:
    return dict(CAPABILITY_REQUEST_SCHEMAS.get(capability, {}))


__all__ = [
    "CAPABILITY_CONFIG_VALIDATORS",
    "CAPABILITY_REQUEST_SCHEMAS",
    "ChatRequestConfig",
    "MasteryPathRequestConfig",
    "VisualizeRequestConfig",
    "build_request_schema",
    "get_capability_request_schema",
    "validate_capability_config",
    "validate_chat_request_config",
    "validate_mastery_path_request_config",
    "validate_visualize_request_config",
]
