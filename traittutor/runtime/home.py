"""Runtime home resolution for installed and source TraitTutor runs."""

from __future__ import annotations

import os
from pathlib import Path

TRAITTUTOR_HOME_ENV = "TRAITTUTOR_HOME"
PACKAGE_ROOT = Path(__file__).resolve().parents[2]


def get_runtime_home(home: str | Path | None = None) -> Path:
    """Return the directory that owns runtime data for this process.

    Priority:
    1. Explicit *home* argument.
    2. ``TRAITTUTOR_HOME`` environment variable.
    3. Current working directory.

    The returned path is the workspace root; runtime data lives below
    ``<home>/data``.
    """

    raw = home if home is not None else os.getenv(TRAITTUTOR_HOME_ENV)
    if raw is None or str(raw).strip() == "":
        return Path.cwd().resolve()
    return Path(raw).expanduser().resolve()


def get_runtime_data_root(home: str | Path | None = None) -> Path:
    """Return ``<runtime-home>/data``."""

    return get_runtime_home(home) / "data"


__all__ = [
    "TRAITTUTOR_HOME_ENV",
    "PACKAGE_ROOT",
    "get_runtime_home",
    "get_runtime_data_root",
]
