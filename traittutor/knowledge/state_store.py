"""Canonical SQLite state for knowledge-base metadata and live progress."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from traittutor.multi_user.context import get_current_user
from traittutor.services.path_service import get_path_service
from traittutor.unified_storage import SectionedRecordStore


class KnowledgeStateStore:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        path_service = get_path_service()
        canonical = path_service.get_knowledge_bases_root()
        self._adapter = SectionedRecordStore(
            "knowledge_state",
            get_current_user().id,
            schema_version=1,
            path_service=path_service if base_dir.resolve() == canonical.resolve() else None,
            db_path=None
            if base_dir.resolve() == canonical.resolve()
            else base_dir / "traittutor.sqlite3",
        )

    def load_config(self) -> dict[str, Any]:
        record = next(iter(self._adapter.snapshot()["config"]), None)
        return dict(record.get("value") or {}) if record else {"knowledge_bases": {}}

    def save_config(self, config: Mapping[str, Any]) -> None:
        owner = get_current_user().id
        with self._adapter.locked() as payload:
            payload["config"] = [
                {"config_id": "knowledge", "owner_id": owner, "value": dict(config)}
            ]
            self._adapter.replace_all(payload)

    def load_metadata(self, kb_name: str) -> dict[str, Any]:
        record = next(
            (
                item
                for item in self._adapter.snapshot()["metadata"]
                if item.get("kb_name") == kb_name
            ),
            None,
        )
        return dict(record.get("value") or {}) if record else {}

    def save_metadata(self, kb_name: str, metadata: Mapping[str, Any]) -> None:
        owner = get_current_user().id
        with self._adapter.locked() as payload:
            payload["metadata"] = [
                item for item in payload["metadata"] if item.get("kb_name") != kb_name
            ]
            payload["metadata"].append(
                {"kb_name": kb_name, "owner_id": owner, "value": dict(metadata)}
            )
            self._adapter.replace_all(payload)

    def load_progress(self, kb_name: str) -> dict[str, Any]:
        record = next(
            (
                item
                for item in self._adapter.snapshot()["progress"]
                if item.get("kb_name") == kb_name
            ),
            None,
        )
        return dict(record.get("value") or {}) if record else {}

    def save_progress(self, kb_name: str, progress: Mapping[str, Any]) -> None:
        owner = get_current_user().id
        with self._adapter.locked() as payload:
            payload["progress"] = [
                item for item in payload["progress"] if item.get("kb_name") != kb_name
            ]
            payload["progress"].append(
                {"kb_name": kb_name, "owner_id": owner, "value": dict(progress)}
            )
            self._adapter.replace_all(payload)

    def delete_progress(self, kb_name: str) -> None:
        with self._adapter.locked() as payload:
            payload["progress"] = [
                item for item in payload["progress"] if item.get("kb_name") != kb_name
            ]
            self._adapter.replace_all(payload)


__all__ = ["KnowledgeStateStore"]
