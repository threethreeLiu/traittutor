from __future__ import annotations

import re
from typing import Any, Iterable
from urllib.parse import urlparse

_ORIGIN_SEPARATORS = re.compile(r"[,;\n]+")


def _raw_origin_items(value: Any) -> Iterable[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        items: list[str] = []
        for item in value:
            items.extend(_raw_origin_items(item))
        return items
    return _ORIGIN_SEPARATORS.split(str(value))


def normalize_origin(value: Any) -> str:
    """Normalize a browser Origin value for CORS allowlists.

    Operators often paste values as ``host:port`` or separate multiple origins
    with semicolons. Browsers always send an Origin as ``scheme://host[:port]``.
    This helper makes common deployment input tolerant while keeping the output
    as exact origins for Starlette's CORSMiddleware.
    """

    origin = str(value or "").strip().rstrip("/")
    if not origin:
        return ""
    if origin in {"*", "null"}:
        return origin
    if "://" not in origin:
        origin = f"http://{origin}"

    try:
        parsed = urlparse(origin)
    except ValueError:
        return origin
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
    return origin


def normalize_origins(value: Any) -> list[str]:
    origins: list[str] = []
    seen: set[str] = set()
    for raw in _raw_origin_items(value):
        origin = normalize_origin(raw)
        if origin and origin not in seen:
            origins.append(origin)
            seen.add(origin)
    return origins


def browser_origins_from_settings(system_settings: dict[str, Any]) -> list[str]:
    """Return the exact browser origins shared by HTTP CORS and WebSockets."""

    frontend_port = str(system_settings["frontend_port"])
    return normalize_origins(
        [
            f"http://localhost:{frontend_port}",
            f"http://127.0.0.1:{frontend_port}",
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            system_settings["cors_origin"],
            system_settings["cors_origins"],
        ]
    )
