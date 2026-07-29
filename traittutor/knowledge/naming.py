"""Knowledge-base name validation helpers."""

from __future__ import annotations

import re
import unicodedata

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_FORBIDDEN_CHARS = set('<>:"/\\|?*#%')
_MAX_KB_NAME_LENGTH = 120


def validate_knowledge_base_name(name: str) -> str:
    """Validate and normalize a user-facing knowledge-base name.

    Names may contain Unicode letters, spaces, dots, hyphens, underscores, and
    common punctuation, but must not contain filesystem or URL-reserved
    separators that would break KB directories or API route paths.
    """
    normalized = unicodedata.normalize("NFC", str(name or "")).strip()
    if not normalized:
        raise ValueError("Knowledge base name is required")
    if normalized in {".", ".."}:
        raise ValueError("Knowledge base name cannot be '.' or '..'")
    if len(normalized) > _MAX_KB_NAME_LENGTH:
        raise ValueError(
            f"Knowledge base name is too long; maximum length is {_MAX_KB_NAME_LENGTH}"
        )
    if _CONTROL_CHARS.search(normalized):
        raise ValueError("Knowledge base name cannot contain control characters")

    forbidden = sorted(ch for ch in _FORBIDDEN_CHARS if ch in normalized)
    if forbidden:
        joined = " ".join(forbidden)
        raise ValueError(
            "Knowledge base name contains reserved characters: "
            f"{joined}. Avoid path or URL separators such as /, \\, ?, #, and %."
        )

    return normalized
