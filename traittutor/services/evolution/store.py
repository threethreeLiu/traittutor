"""Owner-bound append-only persistence for Trail learning evidence."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from traittutor.services.path_service import get_path_service
from traittutor.unified_storage import SectionedRecordStore

from .core import Trail


class TrailStore:
    """Append immutable Trail events without deriving memory or mastery state."""

    def __init__(self, owner_id: str, *, path: Path | None = None) -> None:
        if not owner_id.strip():
            raise ValueError("owner_id is required")
        self.owner_id = owner_id
        self._store_path = path

    def _path(self) -> Path:
        return self._store_path or (get_path_service().get_memory_dir() / "trail" / "events.jsonl")

    def append(self, trail: Trail) -> Trail:
        """Durably append one event, rejecting cross-owner writes."""
        if trail.owner_id not in {None, self.owner_id}:
            raise PermissionError("trail owner does not match bound store")
        row = asdict(trail)
        row["owner_id"] = self.owner_id
        adapter = SectionedRecordStore(
            "evolution_trails",
            self.owner_id,
            schema_version=1,
            path_service=get_path_service() if self._store_path is None else None,
            legacy_path=self._path(),
        )
        with adapter.locked() as payload:
            if not any(item.get("trail_id") == trail.trail_id for item in payload["events"]):
                payload["events"].append(row)
                adapter.replace_all(payload)
        return trail


__all__ = ["TrailStore"]
