"""Durable immutable storage for canonical PageSchema pages."""

from __future__ import annotations

from pathlib import Path
from threading import RLock

from traittutor.multi_user.models import LOCAL_ADMIN_ID
from traittutor.services.path_service import get_path_service
from traittutor.unified_storage import SectionedRecordStore

from .page_schema import PageSchema


class PageStoreError(RuntimeError):
    """The durable page store cannot be read or updated safely."""


class PageStore:
    """Replace-only JSON store for immutable generated pages."""

    _SCHEMA_VERSION = 1

    def __init__(self, *, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else self._default_path()
        self._lock = RLock()
        self._adapter = SectionedRecordStore(
            "page_schemas",
            LOCAL_ADMIN_ID,
            schema_version=self._SCHEMA_VERSION,
            path_service=get_path_service() if path is None else None,
            legacy_path=path,
        )

    @staticmethod
    def _default_path() -> Path:
        return get_path_service().get_workspace_dir() / "traittutor" / "page-schemas.json"

    def _load_unlocked(self) -> dict[str, PageSchema]:
        from traittutor.components.validation import validate_page_schema

        try:
            payload = self._adapter.snapshot()
            # Resolve the key once via ``.get``: a *missing* "pages" key is a
            # legitimate empty store, but indexing payload["pages"] directly
            # raises an uncaught KeyError (not in the except tuple), so every
            # get/save/has would 500 and the store could not self-heal.
            raw_pages = payload.get("pages", []) if isinstance(payload, dict) else None
            if not isinstance(raw_pages, list):
                raise ValueError("page store root is invalid")
            pages = [PageSchema.model_validate(item) for item in raw_pages]
            for page in pages:
                validate_page_schema(page)
        except (OSError, ValueError) as exc:
            raise PageStoreError("page store is unreadable") from exc
        if len({page.page_schema_id for page in pages}) != len(pages):
            raise PageStoreError("page store has duplicate page_schema_ids")
        return {page.page_schema_id: page for page in pages}

    def save(self, schema: PageSchema) -> None:
        """Persist an immutable page, rejecting conflicting replacement."""
        # Re-validate at the write boundary so no hand-built schema can bypass
        # the registered component and property whitelist.
        from traittutor.components.validation import validate_page_schema

        validate_page_schema(schema)
        with self._lock, self._adapter.locked() as payload:
            raw_pages = payload.get("pages", [])
            if not isinstance(raw_pages, list):
                raise PageStoreError("page store root is invalid")
            pages = {
                page.page_schema_id: page
                for page in (PageSchema.model_validate(item) for item in raw_pages)
            }
            existing = pages.get(schema.page_schema_id)
            if existing is not None and existing != schema:
                raise PageStoreError("page_schema_id already has different immutable content")
            pages[schema.page_schema_id] = schema
            payload["pages"] = [page.model_dump(mode="json") for _, page in sorted(pages.items())]
            self._adapter.replace_all(payload)

    def get(self, page_schema_id: str) -> PageSchema | None:
        with self._lock:
            return self._load_unlocked().get(page_schema_id)

    def has(self, page_schema_id: str) -> bool:
        return self.get(page_schema_id) is not None


__all__ = ["PageStore", "PageStoreError"]
