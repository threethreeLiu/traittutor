"""
Shared notebook manager backed by the canonical unified SQLite database.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
import threading
import time
import uuid

from pydantic import BaseModel

from traittutor.multi_user.models import LOCAL_ADMIN_ID
from traittutor.services.llm import clean_thinking_tags
from traittutor.services.path_service import get_path_service
from traittutor.unified_storage import SectionedRecordStore


class RecordType(str, Enum):
    """Notebook record type.

    Only the four live types are members. :class:`NotebookRecord.type`
    stays a free-form ``str`` so old persisted records still load — this enum is
    used only to normalize the *write* path in :func:`NotebookManager.add_record`.
    """

    SOLVE = "solve"
    QUESTION = "question"
    RESEARCH = "research"
    CHAT = "chat"


class NotebookRecord(BaseModel):
    """Single record stored in a notebook."""

    id: str
    type: str
    title: str
    summary: str = ""
    user_query: str
    output: str
    metadata: dict = {}
    created_at: float
    kb_name: str | None = None


class Notebook(BaseModel):
    """Notebook model."""

    id: str
    name: str
    description: str = ""
    created_at: float
    updated_at: float
    records: list[NotebookRecord] = []
    color: str = "#3B82F6"
    icon: str = "book"


_UNSET = object()
_notebook_lock = threading.RLock()


def _clean_record_summary(summary: str) -> str:
    """Remove private model scratchpads before notebook summaries are persisted."""
    return clean_thinking_tags(str(summary or "")).strip()


class NotebookManager:
    """Manage notebook aggregates in the workspace's unified database."""

    def __init__(self, base_dir: str | None = None):
        if base_dir is None:
            path_service = get_path_service()
            base_dir_path = path_service.get_notebook_dir()
        else:
            base_dir_path = Path(base_dir)

        self.base_dir = base_dir_path
        self.index_file = self.base_dir / "notebooks_index.json"
        self._adapter = SectionedRecordStore(
            "notebooks",
            LOCAL_ADMIN_ID,
            schema_version=1,
            path_service=get_path_service() if base_dir is None else None,
            legacy_path=self.base_dir / "notebooks.json",
        )

    def _ensure_index(self) -> None:
        """Retained as a no-op for callers from the former file implementation."""

    def _load_index(self) -> dict:
        return {"notebooks": [self._summary(item) for item in self._items()]}

    def _save_index(self, index: dict) -> None:
        # The index is now a derived view of notebook aggregates.
        del index

    def _get_notebook_file(self, notebook_id: str) -> Path:
        return self.base_dir / f"{notebook_id}.json"

    def _load_notebook(self, notebook_id: str) -> dict | None:
        notebook = next((item for item in self._items() if item.get("id") == notebook_id), None)
        if notebook is None:
            return None
        if self._sanitize_loaded_notebook(notebook):
            try:
                self._save_notebook(notebook)
            except Exception:
                pass
        return notebook

    def _sanitize_loaded_notebook(self, notebook: dict) -> bool:
        changed = False
        records = notebook.get("records", [])
        if not isinstance(records, list):
            return False
        for record in records:
            if not isinstance(record, dict):
                continue
            raw_summary = record.get("summary", "")
            cleaned = _clean_record_summary(raw_summary)
            if cleaned != raw_summary:
                record["summary"] = cleaned
                changed = True
        return changed

    def _save_notebook(self, notebook: dict) -> None:
        with _notebook_lock:
            with self._adapter.locked() as payload:
                payload["notebooks"] = [
                    item for item in payload["notebooks"] if item.get("id") != notebook["id"]
                ]
                payload["notebooks"].append(notebook)
                self._adapter.replace_all(payload)

    def _items(self) -> list[dict]:
        return self._adapter.snapshot()["notebooks"]

    @staticmethod
    def _summary(notebook: dict) -> dict:
        return {
            "id": notebook["id"],
            "name": notebook["name"],
            "description": notebook.get("description", ""),
            "created_at": notebook["created_at"],
            "updated_at": notebook["updated_at"],
            "record_count": len(notebook.get("records", [])),
            "color": notebook.get("color", "#3B82F6"),
            "icon": notebook.get("icon", "book"),
        }

    def _touch_index_entry(self, notebook_id: str, notebook: dict) -> None:
        del notebook_id, notebook

    # === Notebook Operations ===

    def create_notebook(
        self, name: str, description: str = "", color: str = "#3B82F6", icon: str = "book"
    ) -> dict:
        notebook_id = str(uuid.uuid4())[:8]
        now = time.time()

        notebook = {
            "id": notebook_id,
            "name": name,
            "description": description,
            "created_at": now,
            "updated_at": now,
            "records": [],
            "color": color,
            "icon": icon,
        }

        self._save_notebook(notebook)

        return notebook

    def list_notebooks(self) -> list[dict]:
        notebooks = [self._summary(notebook) for notebook in self._items()]

        notebooks.sort(key=lambda x: x["updated_at"], reverse=True)
        return notebooks

    def get_notebook(self, notebook_id: str) -> dict | None:
        return self._load_notebook(notebook_id)

    def update_notebook(
        self,
        notebook_id: str,
        name: str | None = None,
        description: str | None = None,
        color: str | None = None,
        icon: str | None = None,
    ) -> dict | None:
        notebook = self._load_notebook(notebook_id)
        if not notebook:
            return None

        if name is not None:
            notebook["name"] = name
        if description is not None:
            notebook["description"] = description
        if color is not None:
            notebook["color"] = color
        if icon is not None:
            notebook["icon"] = icon

        notebook["updated_at"] = time.time()
        self._save_notebook(notebook)
        self._touch_index_entry(notebook_id, notebook)
        return notebook

    def delete_notebook(self, notebook_id: str) -> bool:
        with _notebook_lock:
            with self._adapter.locked() as payload:
                remaining = [item for item in payload["notebooks"] if item.get("id") != notebook_id]
                if len(remaining) == len(payload["notebooks"]):
                    return False
                payload["notebooks"] = remaining
                self._adapter.replace_all(payload)
                return True

    # === Record Operations ===

    def add_record(
        self,
        notebook_ids: list[str],
        record_type: RecordType | str,
        title: str,
        user_query: str,
        output: str,
        summary: str = "",
        metadata: dict | None = None,
        kb_name: str | None = None,
    ) -> dict:
        record_id = str(uuid.uuid4())[:8]
        now = time.time()
        # Accept both enum instances and plain string values from callers.
        resolved_type = (
            record_type if isinstance(record_type, RecordType) else RecordType(str(record_type))
        )

        record = {
            "id": record_id,
            "type": resolved_type,
            "title": title,
            "summary": _clean_record_summary(summary),
            "user_query": user_query,
            "output": output,
            "metadata": metadata or {},
            "created_at": now,
            "kb_name": kb_name,
        }

        added_to: list[str] = []
        for notebook_id in notebook_ids:
            notebook = self._load_notebook(notebook_id)
            if not notebook:
                continue
            notebook["records"].append(record)
            notebook["updated_at"] = now
            self._save_notebook(notebook)
            self._touch_index_entry(notebook_id, notebook)
            added_to.append(notebook_id)

        return {"record": record, "added_to_notebooks": added_to}

    def get_records(self, notebook_id: str, record_ids: list[str] | None = None) -> list[dict]:
        notebook = self._load_notebook(notebook_id)
        if not notebook:
            return []

        records = list(notebook.get("records", []))
        if not record_ids:
            return records

        wanted = set(record_ids)
        return [record for record in records if str(record.get("id", "")) in wanted]

    def get_record(self, notebook_id: str, record_id: str) -> dict | None:
        records = self.get_records(notebook_id, [record_id])
        return records[0] if records else None

    def update_record(
        self,
        notebook_id: str,
        record_id: str,
        *,
        title: str | None = None,
        summary: str | None = None,
        user_query: str | None = None,
        output: str | None = None,
        metadata: dict | None = None,
        kb_name: str | None | object = _UNSET,
    ) -> dict | None:
        notebook = self._load_notebook(notebook_id)
        if not notebook:
            return None

        updated_record: dict | None = None
        for record in notebook.get("records", []):
            if str(record.get("id", "")) != str(record_id):
                continue
            if title is not None:
                record["title"] = title
            if summary is not None:
                record["summary"] = _clean_record_summary(summary)
            if user_query is not None:
                record["user_query"] = user_query
            if output is not None:
                record["output"] = output
            if metadata is not None:
                current_metadata = record.get("metadata", {}) or {}
                record["metadata"] = {**current_metadata, **metadata}
            if kb_name is not _UNSET:
                record["kb_name"] = kb_name
            updated_record = record
            break

        if updated_record is None:
            return None

        notebook["updated_at"] = time.time()
        self._save_notebook(notebook)
        self._touch_index_entry(notebook_id, notebook)
        return updated_record

    def get_records_by_references(self, notebook_references: list[dict]) -> list[dict]:
        resolved: list[dict] = []

        for ref in notebook_references:
            notebook_id = str(ref.get("notebook_id", "") or "").strip()
            if not notebook_id:
                continue
            record_ids = [
                str(record_id).strip()
                for record_id in (ref.get("record_ids") or [])
                if str(record_id).strip()
            ]
            notebook = self._load_notebook(notebook_id)
            if not notebook:
                continue

            notebook_name = str(notebook.get("name", "") or notebook_id)
            for record in self.get_records(notebook_id, record_ids):
                resolved.append(
                    {
                        **record,
                        "notebook_id": notebook_id,
                        "notebook_name": notebook_name,
                    }
                )

        return resolved

    def remove_record(self, notebook_id: str, record_id: str) -> bool:
        notebook = self._load_notebook(notebook_id)
        if not notebook:
            return False

        original_count = len(notebook["records"])
        notebook["records"] = [r for r in notebook["records"] if r["id"] != record_id]

        if len(notebook["records"]) == original_count:
            return False

        notebook["updated_at"] = time.time()
        self._save_notebook(notebook)
        self._touch_index_entry(notebook_id, notebook)
        return True

    def get_statistics(self) -> dict:
        notebooks = self.list_notebooks()

        total_records = 0
        type_counts = {
            "solve": 0,
            "question": 0,
            "research": 0,
            "chat": 0,
        }

        for nb_info in notebooks:
            notebook = self._load_notebook(nb_info["id"])
            if notebook:
                for record in notebook.get("records", []):
                    total_records += 1
                    record_type = record.get("type", "")
                    if record_type in type_counts:
                        type_counts[record_type] += 1

        return {
            "total_notebooks": len(notebooks),
            "total_records": total_records,
            "records_by_type": type_counts,
            "recent_notebooks": notebooks[:5],
        }


_instances: dict[str, NotebookManager] = {}


def get_notebook_manager() -> NotebookManager:
    base_dir = get_path_service().get_notebook_dir().resolve()
    key = str(base_dir)
    if key not in _instances:
        _instances[key] = NotebookManager()
    return _instances[key]


class _NotebookManagerProxy:
    def __getattr__(self, name: str):
        return getattr(get_notebook_manager(), name)


notebook_manager = _NotebookManagerProxy()
