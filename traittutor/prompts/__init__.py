"""Canonical, checked-in prompt assets.

Prompt text is kept outside feature packages so it can be reviewed, localized,
versioned, and reused without coupling an asset to the Python implementation
that happens to consume it.
"""

from pathlib import Path

PROMPTS_ROOT = Path(__file__).resolve().parent


def asset_path(*parts: str) -> Path:
    """Return a path below the canonical prompt root."""
    return PROMPTS_ROOT.joinpath(*parts)


__all__ = ["PROMPTS_ROOT", "asset_path"]
