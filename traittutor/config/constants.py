#!/usr/bin/env python
"""
Constants for TraitTutor
"""

from pathlib import Path

# Project root directory - central location for all path calculations
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Valid tools for investigate agent
VALID_INVESTIGATE_TOOLS = ["rag", "web_search", "none"]

# Valid tools for solve agent
VALID_SOLVE_TOOLS = [
    "web_search",
    "code_execution",
    "rag",
    "none",
    "finish",
]

# Standard stdlib log level tags.
LOG_LEVEL_TAGS = [
    "DEBUG",
    "INFO",
    "WARNING",
    "ERROR",
    "CRITICAL",
]
