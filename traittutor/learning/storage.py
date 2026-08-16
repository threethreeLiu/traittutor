from __future__ import annotations

from pathlib import Path
import threading
import time

from traittutor.learning.models import LearningProgress
from traittutor.multi_user.models import LOCAL_ADMIN_ID
from traittutor.services.path_service import PathService, get_path_service
from traittutor.unified_storage import SectionedRecordStore

# Module-level lock so CAS semantics hold across all store instances.
_cas_lock = threading.Lock()


class LearningStore:
    def __init__(
        self,
        root: Path | None = None,
        *,
        path_service: PathService | None = None,
        owner_id: str = LOCAL_ADMIN_ID,
    ) -> None:
        resolved_path_service: PathService | None
        if root is None:
            resolved_path_service = path_service or get_path_service()
            self._root = resolved_path_service.get_workspace_dir() / "learning"
        else:
            resolved_path_service = path_service
            self._root = root
        self._adapter = SectionedRecordStore(
            "learning_progress",
            owner_id,
            schema_version=1,
            path_service=resolved_path_service,
            legacy_path=(
                None if resolved_path_service is not None else self._root / "learning-progress.json"
            ),
        )

    @property
    def root(self) -> Path:
        return self._root

    def _path(self, book_id: str) -> Path:
        if "/" in book_id or "\\" in book_id or ".." in book_id or ":" in book_id:
            raise ValueError(f"Invalid book_id: {book_id!r}")
        return self._root / f"{book_id}.json"

    def save(self, progress: LearningProgress) -> None:
        with _cas_lock:
            with self._adapter.locked() as payload:
                progress.updated_at = time.time()
                progress.version += 1
                records = payload["progress"]
                records[:] = [item for item in records if item.get("book_id") != progress.book_id]
                records.append(progress.model_dump(mode="json"))
                self._adapter.replace_all(payload)

    def load(self, book_id: str) -> LearningProgress | None:
        self._path(book_id)  # validate the identifier at the boundary
        record = next(
            (
                item
                for item in self._adapter.snapshot()["progress"]
                if item.get("book_id") == book_id
            ),
            None,
        )
        return LearningProgress.model_validate(record) if record is not None else None

    def delete(self, book_id: str) -> None:
        with _cas_lock:
            self._path(book_id)
            with self._adapter.locked() as payload:
                payload["progress"] = [
                    item for item in payload["progress"] if item.get("book_id") != book_id
                ]
                self._adapter.replace_all(payload)

    def exists(self, book_id: str) -> bool:
        return self.load(book_id) is not None

    def list_all(self) -> list[str]:
        """Return all book_ids that have stored progress."""
        return sorted(str(item["book_id"]) for item in self._adapter.snapshot()["progress"])


__all__ = ["LearningStore"]
