"""Shared header hygiene for serving user/model-produced files over HTTP.

The web frontend proxies ``/api/*`` same-origin to this backend, so any file
served inline executes in the application's origin (cookies included). These
helpers neutralize *active content* — HTML/SVG/XML that browsers render as
documents and can carry script — while leaving passive preview (images, PDF,
audio/video) intact.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

# Extensions a browser renders as an executable document. Serving any of
# these inline from our origin is a stored-XSS vector (see the avatar
# endpoint, which rejects SVG for exactly this reason).
ACTIVE_CONTENT_SUFFIXES = frozenset({".html", ".htm", ".xhtml", ".svg", ".xml"})

# Hard sandbox for served artifacts: no script, no network, no origin
# privileges — even a mis-guessed content type cannot execute anything.
SANDBOX_CSP = "default-src 'none'; sandbox"


def is_active_content(name: str | Path) -> bool:
    """True when ``name``'s extension marks it as browser-executable content."""
    return Path(name).suffix.lower() in ACTIVE_CONTENT_SUFFIXES


def forced_download_media_type(name: str | Path) -> str:
    """Media type that prevents document rendering for active content."""
    return "application/octet-stream" if is_active_content(name) else ""


def content_disposition(filename: str, *, disposition: str = "inline") -> str:
    """Build a Content-Disposition header that survives non-ASCII filenames.

    HTTP/1.1 headers are latin-1, so a Chinese / accented filename needs the
    RFC 6266 / 5987 shape: ``filename*=UTF-8''<percent-encoded>`` plus an
    ASCII fallback on ``filename=`` for legacy clients.
    """
    ascii_fallback = filename.encode("ascii", errors="replace").decode("ascii")
    # Quotes / backslashes break the simple-quoted-string form; collapse them.
    ascii_fallback = ascii_fallback.replace('"', "_").replace("\\", "_")
    encoded = quote(filename, safe="")
    return f"{disposition}; filename=\"{ascii_fallback}\"; filename*=UTF-8''{encoded}"


__all__ = [
    "ACTIVE_CONTENT_SUFFIXES",
    "SANDBOX_CSP",
    "content_disposition",
    "forced_download_media_type",
    "is_active_content",
]
