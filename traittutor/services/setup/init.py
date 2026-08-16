#!/usr/bin/env python
"""
System Setup and Initialization
Combines user directory initialization and port configuration management.
"""

import logging
from pathlib import Path

from traittutor.services.path_service import get_path_service

# Initialize logger for setup operations
_setup_logger = None

DEFAULT_INTERFACE_SETTINGS = {
    # Snow is the default for new installs.
    "theme": "snow",
    "language": "zh",
    "sidebar_description": "✨ TraitTutor",
    "sidebar_nav_order": {
        "start": ["/", "/history"],
        "learnResearch": ["/question", "/solver", "/research"],
    },
}

DEFAULT_MAIN_SETTINGS = {
    "system": {
        "language": "zh",
    },
    "logging": {
        "level": "WARNING",
        "save_to_file": True,
        "console_output": True,
    },
    "tools": {
        "run_code": {
            "allowed_roots": ["./data/user"],
        },
        "web_search": {
            "enabled": True,
        },
    },
    "capabilities": {
        "solve": {
            "max_rounds": 12,
            "max_replans": 2,
        },
        "research": {
            "researching": {
                "note_agent_mode": "auto",
                "tool_timeout": 60,
                "tool_max_retries": 2,
                "paper_search_years_limit": 3,
            },
        },
        "question": {
            "exploring": {
                "max_iterations": 8,
                "tool_summarizer": {
                    "enabled": True,
                    "max_tokens": 800,
                },
            },
        },
    },
}

DEFAULT_AGENTS_SETTINGS = {
    "capabilities": {
        "solve": {"temperature": 0.3, "max_tokens": 8192},
        "research": {"temperature": 0.5, "max_tokens": 12000},
        "question": {"temperature": 0.7, "max_tokens": 4096},
        "visualize": {"temperature": 0.4, "max_tokens": 16384},
        "chat": {
            "temperature": 0.2,
            "responding": {"max_tokens": 8000},
        },
    },
    "tools": {
        "brainstorm": {"temperature": 0.8, "max_tokens": 2048},
    },
    "services": {
        "personalization": {"temperature": 0.5, "max_tokens": 8192},
    },
    "plugins": {
        "vision_solver": {"temperature": 0.3, "max_tokens": 12000},
        "math_animator": {"temperature": 0.4, "max_tokens": 12000},
    },
}


def _get_setup_logger():
    """Get logger for setup operations"""
    global _setup_logger
    if _setup_logger is None:
        _setup_logger = logging.getLogger(__name__)
    return _setup_logger


# ============================================================================
# User Directory Initialization
# ============================================================================


def init_user_directories(project_root: Path | None = None) -> None:
    """
    Initialize essential user data files if they don't exist.

    This function uses lazy initialization - directories are created on-demand
    when files are saved, rather than pre-creating all directories at startup.

    Only essential configuration files (like settings/interface.json) are
    created at startup if they don't exist.

    Directory structure (created on-demand by each module):
    data/user/
    ├── logs/
    └── workspace/
        ├── traittutor/traittutor.sqlite3
        ├── notebook/
        ├── memory/
        ├── book/
        └── chat/
            ├── chat/
            ├── deep_solve/
            ├── deep_question/
            ├── deep_research/
            ├── math_animator/
            └── _detached_code_execution/

    Args:
        project_root: Project root directory (ignored, kept for API compatibility)
    """
    # Use PathService for all paths
    path_service = get_path_service()
    path_service.ensure_all_directories()

    # Only initialize essential configuration files
    # Directories will be created on-demand when files are saved
    _ensure_essential_settings(path_service)


def _ensure_essential_settings(path_service) -> None:
    """
    Ensure essential settings files exist.

    This is the minimal initialization needed at startup.
    All other directories are created on-demand when files are saved.
    """
    from traittutor.services.config.loader import load_runtime_document

    load_runtime_document("main")
    load_runtime_document("agents")

    try:
        from traittutor.services.config import ensure_runtime_settings_files

        ensure_runtime_settings_files()
    except Exception as e:
        _get_setup_logger().warning(f"Failed to initialise runtime SQLite settings: {e}")


# ============================================================================
# Port Configuration Management
# ============================================================================
# Ports are configured in the canonical SQLite runtime-settings document.
# ============================================================================


def get_backend_port(project_root: Path | None = None) -> int:
    """
    Get backend port from runtime settings.

    Returns:
        Backend port number (default: 8001)
    """
    try:
        from traittutor.services.config.launch_settings import load_launch_settings

        return load_launch_settings(project_root).backend_port
    except Exception as exc:
        logger = _get_setup_logger()
        logger.warning(f"Failed to load backend port from runtime settings: {exc}")
        return 8001


def get_frontend_port(project_root: Path | None = None) -> int:
    """
    Get frontend port from runtime settings.

    Returns:
        Frontend port number (default: 3782)
    """
    try:
        from traittutor.services.config.launch_settings import load_launch_settings

        return load_launch_settings(project_root).frontend_port
    except Exception as exc:
        logger = _get_setup_logger()
        logger.warning(f"Failed to load frontend port from runtime settings: {exc}")
        return 3782


def get_ports(project_root: Path | None = None) -> tuple[int, int]:
    """
    Get both backend and frontend ports from configuration.

    Args:
        project_root: Project root directory (if None, will try to detect)

    Returns:
        Tuple of (backend_port, frontend_port)

    Raises:
        SystemExit: If ports are not configured
    """
    backend_port = get_backend_port(project_root)
    frontend_port = get_frontend_port(project_root)
    return (backend_port, frontend_port)


__all__ = [
    # User directory initialization
    "init_user_directories",
    # Port configuration
    "get_backend_port",
    "get_frontend_port",
    "get_ports",
]
