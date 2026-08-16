#!/usr/bin/env python
"""
Configuration Loader
====================

Unified configuration loading for all TraitTutor modules.
Provides YAML configuration loading, path resolution, and language parsing.
"""

import asyncio
from copy import deepcopy
from pathlib import Path
from typing import Any

from traittutor.multi_user.models import LOCAL_ADMIN_ID
from traittutor.runtime.home import get_runtime_home
from traittutor.services.path_service import get_path_service
from traittutor.unified_storage import SQLiteDocumentStore

# Runtime workspace root. Application settings live under PROJECT_ROOT/data/user/settings.
PROJECT_ROOT = get_runtime_home()


def get_runtime_settings_dir(project_root: Path | None = None) -> Path:
    """Return the canonical runtime settings directory under ``data/user/settings``."""
    root = project_root or PROJECT_ROOT
    return root / "data" / "user" / "settings"


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """
    Deep merge two dictionaries, values in override will override values in base

    Args:
        base: Base configuration
        override: Override configuration

    Returns:
        Merged configuration
    """
    result = base.copy()

    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            # Recursively merge dictionaries
            result[key] = _deep_merge(result[key], value)
        else:
            # Direct override
            result[key] = value

    return result


def _runtime_document_store(project_root: Path | None = None) -> SQLiteDocumentStore:
    root = project_root or PROJECT_ROOT
    return SQLiteDocumentStore(
        LOCAL_ADMIN_ID,
        namespace="runtime-config",
        db_path=root / "data" / "user" / "workspace" / "traittutor" / "traittutor.sqlite3",
    )


def _runtime_document_defaults(name: str) -> dict[str, Any]:
    from traittutor.services.setup.init import DEFAULT_AGENTS_SETTINGS, DEFAULT_MAIN_SETTINGS

    defaults = {"main": DEFAULT_MAIN_SETTINGS, "agents": DEFAULT_AGENTS_SETTINGS}
    if name not in defaults:
        raise ValueError(f"Unsupported runtime configuration document: {name}")
    return deepcopy(defaults[name])


def load_runtime_document(name: str, project_root: Path | None = None) -> dict[str, Any]:
    normalized_name = name.removesuffix(".yaml")
    defaults = _runtime_document_defaults(normalized_name)
    stored = _runtime_document_store(project_root).load(normalized_name, None)
    if stored is None:
        _runtime_document_store(project_root).save(normalized_name, defaults)
        return defaults
    if not isinstance(stored, dict):
        raise ValueError(f"Runtime configuration {normalized_name} must be an object")
    return _deep_merge(defaults, stored)


def save_runtime_document(
    name: str, payload: dict[str, Any], project_root: Path | None = None
) -> dict[str, Any]:
    normalized_name = name.removesuffix(".yaml")
    normalized = _deep_merge(_runtime_document_defaults(normalized_name), payload)
    _runtime_document_store(project_root).save(normalized_name, normalized)
    return normalized


def _inject_runtime_paths(config: dict[str, Any]) -> dict[str, Any]:
    """Expose canonical runtime paths without treating YAML paths as user-editable state."""
    path_service = get_path_service()
    normalized = dict(config or {})
    tools = dict(normalized.get("tools", {}) or {})
    run_code = dict(tools.get("run_code", {}) or {})
    run_code["workspace"] = str(path_service.get_chat_feature_dir("_detached_code_execution"))
    tools["run_code"] = run_code
    normalized["tools"] = tools
    normalized["paths"] = {
        "user_data_dir": str(path_service.get_user_root()),
        "knowledge_bases_dir": str(path_service.get_knowledge_bases_root()),
        "user_log_dir": str(path_service.get_logs_dir()),
        "performance_log_dir": str(path_service.get_logs_dir() / "performance"),
        "question_output_dir": str(path_service.get_chat_feature_dir("deep_question")),
        "research_output_dir": str(path_service.get_research_dir()),
        "research_reports_dir": str(path_service.get_research_reports_dir()),
        "solve_output_dir": str(path_service.get_chat_feature_dir("deep_solve")),
    }
    return normalized


def load_config_with_main(config_file: str, project_root: Path | None = None) -> dict[str, Any]:
    """
    Load configuration file, automatically merge with main.yaml common configuration

    Args:
        config_file: Configuration file name (e.g., "main.yaml")
        project_root: Project root directory (if None, will try to auto-detect)

    Returns:
        Merged configuration dictionary
    """
    if project_root is None:
        project_root = PROJECT_ROOT

    return _inject_runtime_paths(load_runtime_document(config_file, project_root))


async def load_config_with_main_async(
    config_file: str, project_root: Path | None = None
) -> dict[str, Any]:
    """
    Async version of load_config_with_main for non-blocking file operations.

    Load configuration file, automatically merge with main.yaml common configuration

    Args:
        config_file: Configuration file name (e.g., "main.yaml")
        project_root: Project root directory (if None, will try to auto-detect)

    Returns:
        Merged configuration dictionary
    """
    if project_root is None:
        project_root = PROJECT_ROOT

    return await asyncio.to_thread(load_config_with_main, config_file, project_root)


def get_path_from_config(config: dict[str, Any], path_key: str, default: str = "") -> str:
    """
    Get path from configuration.

    Args:
        config: Configuration dictionary
        path_key: Path key name (e.g., "log_dir", "workspace")
        default: Default value

    Returns:
        Path string
    """
    injected = _inject_runtime_paths(config)
    if "paths" in injected and path_key in injected["paths"]:
        return injected["paths"][path_key]
    if path_key == "workspace":
        return str(injected.get("tools", {}).get("run_code", {}).get("workspace") or default)
    return str(default)


def parse_language(language: Any) -> str:
    """
    Unified language configuration parser, supports multiple input formats

    Supported language representations:
    - English: "en", "english", "English"
    - Chinese: "zh", "chinese", "Chinese"

    Args:
        language: Language configuration value (can be "zh"/"en"/"Chinese"/"English" etc.)

    Returns:
        Standardized language code: 'zh' or 'en', defaults to 'zh'
    """
    if not language:
        return "zh"

    if isinstance(language, str):
        lang_lower = language.lower()
        if lang_lower in ["en", "english"]:
            return "en"
        if lang_lower in ["zh", "chinese", "cn"]:
            return "zh"

    return "zh"  # Default Chinese


def get_agent_params(module_name: str) -> dict:
    """
    Get agent parameters (temperature, max_tokens) for a specific module.

    This function loads parameters from config/agents.yaml which serves as the
    SINGLE source of truth for all agent temperature and max_tokens settings.

    Args:
        module_name: Module name, one of:
            - "solve": Solve module agents
            - "research": Research module agents
            - "question": Question module agents
            - "brainstorm": Brainstorm tool settings
            - "narrator": Narrator agent (independent, for TTS)
            - "llm_probe": Settings → LLM diagnostic probe

    Returns:
        dict: Dictionary containing:
            - temperature: float, default 0.5
            - max_tokens: int, default 4096

    Example:
        >>> params = get_agent_params("solve")
        >>> params["temperature"]  # 0.3
        >>> params["max_tokens"]   # 8192
    """
    global_defaults = {
        "temperature": 0.5,
        "max_tokens": 4096,
    }
    section_map = {
        "solve": ("capabilities", "solve"),
        "research": ("capabilities", "research"),
        "question": ("capabilities", "question"),
        "visualize": ("capabilities", "visualize"),
        "brainstorm": ("tools", "brainstorm"),
        "vision_solver": ("plugins", "vision_solver"),
        "math_animator": ("plugins", "math_animator"),
        "llm_probe": ("diagnostics", "llm_probe"),
    }
    section = section_map.get(module_name)
    if section is None:
        return global_defaults

    # Per-module defaults come from the shipped DEFAULT_AGENTS_SETTINGS so that
    # adding a new capability seeded with non-default tokens (e.g. visualize at
    # 16k) doesn't require existing users to hand-edit their stale agents.yaml.
    # Imported lazily to avoid a circular dependency with services.setup.
    from traittutor.services.setup.init import DEFAULT_AGENTS_SETTINGS

    seeded: dict[str, Any] = DEFAULT_AGENTS_SETTINGS
    for key in section:
        seeded = seeded.get(key, {}) if isinstance(seeded, dict) else {}
    module_defaults = {
        "temperature": seeded.get("temperature", global_defaults["temperature"])
        if isinstance(seeded, dict)
        else global_defaults["temperature"],
        "max_tokens": seeded.get("max_tokens", global_defaults["max_tokens"])
        if isinstance(seeded, dict)
        else global_defaults["max_tokens"],
    }

    agents_config = load_runtime_document("agents", PROJECT_ROOT)
    module_config: dict[str, Any] = agents_config
    for key in section:
        module_config = module_config.get(key, {}) if isinstance(module_config, dict) else {}
    return {
        "temperature": module_config.get("temperature", module_defaults["temperature"]),
        "max_tokens": module_config.get("max_tokens", module_defaults["max_tokens"]),
    }


DEFAULT_CHAT_PARAMS: dict[str, Any] = {
    "temperature": 0.2,
    # Exploring-loop budget: max LLM rounds in one turn's loop (a round
    # without tool calls ends the loop early — the normal exit).
    "max_rounds": 8,
    "exploring": {"max_tokens": 1600},
    "responding": {"max_tokens": 8000},
}


def get_chat_params() -> dict[str, Any]:
    """
    Read ``capabilities.chat`` from agents.yaml with deep-merged defaults.

    Unlike :func:`get_agent_params`, the chat capability has per-stage
    sub-sections (``exploring``, ``responding``), each with its own
    ``max_tokens``. A single ``temperature`` and round budget are shared
    across the chat loop. Legacy keys from the targeting-era schema
    (``max_iterations``, ``max_explore_rounds``, …) are filtered out.

    Returns:
        dict: Deep-merged chat configuration. Always contains every stage key
        from :data:`DEFAULT_CHAT_PARAMS` so callers can index without checks.
    """
    agents_config = load_runtime_document("agents", PROJECT_ROOT)
    cfg: dict[str, Any] = (agents_config.get("capabilities", {}) or {}).get("chat", {}) or {}
    known_keys = set(DEFAULT_CHAT_PARAMS)
    filtered_cfg = {key: value for key, value in cfg.items() if key in known_keys}
    return _deep_merge(DEFAULT_CHAT_PARAMS, filtered_cfg)


__all__ = [
    "PROJECT_ROOT",
    "get_runtime_settings_dir",
    "load_runtime_document",
    "save_runtime_document",
    "load_config_with_main",
    "get_path_from_config",
    "parse_language",
    "get_agent_params",
    "get_chat_params",
    "DEFAULT_CHAT_PARAMS",
    "_deep_merge",
]
