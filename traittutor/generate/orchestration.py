"""Generation-domain orchestration primitives.

The provider is injected so tool calls and image generation remain testable;
the generator never treats an ungrounded tool result as source material.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Awaitable, Callable, Mapping


@dataclass(frozen=True)
class SupplementChunk:
    chunk_id: str
    text: str
    source_id: str
    citation: dict[str, Any]
    provenance: str = "supplement"


ToolQuery = Callable[[str, Mapping[str, Any]], Awaitable[list[SupplementChunk]]]


async def supplement_material(gap: str, context: Mapping[str, Any], query: ToolQuery | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if query is None or not gap.strip():
        return [], []
    chunks = await query(gap, context)
    grounded = [asdict(chunk) for chunk in chunks if chunk.text.strip() and chunk.citation]
    return grounded, [{"tool": "supplement_material", "query": gap, "result_count": len(grounded)}]


def assemble_assets(items: list[dict[str, Any]], assets: Mapping[str, Mapping[str, Any]] | None = None) -> dict[str, Any]:
    assets = assets or {}
    missing: list[str] = []
    assembled = []
    for item in items:
        key = str(item.get("asset_id") or item.get("node_id") or item.get("question_id"))
        asset = assets.get(key)
        copy = dict(item)
        copy["asset"] = dict(asset) if asset else None
        if not asset:
            missing.append(key)
        assembled.append(copy)
    return {"items": assembled, "asset_status": "degraded" if missing else "completed", "missing_asset_ids": missing}
