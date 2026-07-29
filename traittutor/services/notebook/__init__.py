"""Shared notebook service used by CLI, Web, and runtime."""

from .service import (
    Notebook,
    NotebookManager,
    NotebookRecord,
    RecordType,
    get_notebook_manager,
    notebook_manager,
)

__all__ = [
    "Notebook",
    "NotebookManager",
    "NotebookRecord",
    "RecordType",
    "get_notebook_manager",
    "notebook_manager",
]
