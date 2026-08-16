from __future__ import annotations

import json
from pathlib import Path

import pytest

from traittutor.components import ComponentInstance, PageRegion, PageSchema, PageStore
from traittutor.components.page_store import PageStoreError

CREATED = "2026-08-09T08:00:00+00:00"


def _page() -> PageSchema:
    return PageSchema(
        page_schema_id="run:page",
        generation_run_id="run",
        version="v1",
        regions=[
            PageRegion(
                region_id="r1",
                component=ComponentInstance(
                    instance_id="run:page:r1",
                    component_type="concept_explanation",
                    version="v1",
                    props={"title": "Title", "body_markdown": "Body"},
                ),
            )
        ],
        created_at=CREATED,
    )


def test_save_then_get_round_trips_identical_page(tmp_path: Path) -> None:
    store = PageStore(path=tmp_path / "pages.json")
    page = _page()
    store.save(page)
    recovered = store.get(page.page_schema_id)
    assert recovered is not None
    assert recovered.model_dump(mode="json") == page.model_dump(mode="json")


def test_unknown_page_is_absent(tmp_path: Path) -> None:
    store = PageStore(path=tmp_path / "pages.json")
    assert store.get("missing") is None
    assert not store.has("missing")


def test_second_store_instance_reads_persisted_page(tmp_path: Path) -> None:
    path = tmp_path / "pages.json"
    first = PageStore(path=path)
    second = PageStore(path=path)
    first.save(_page())
    assert second.has("run:page")
    assert second.get("run:page") == _page()


def test_missing_pages_key_loads_empty_not_keyerror(tmp_path: Path) -> None:
    # Regression for code-review finding #3: a persisted store whose payload
    # is missing the "pages" key (truncated/partial write) must self-heal to
    # empty rather than raise an uncaught KeyError on every get/save/has.
    path = tmp_path / "pages.json"
    path.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
    store = PageStore(path=path)
    assert store.get("anything") is None
    assert not store.has("anything")
    # The store must remain writable after healing.
    store.save(_page())
    assert store.has("run:page")


def test_read_rejects_noncanonical_stored_component(tmp_path: Path) -> None:
    store = PageStore(path=tmp_path / "pages.json")
    invalid = _page().model_dump(mode="json")
    invalid["regions"][0]["component"]["props"]["html"] = "<script>alert(1)</script>"
    with store._adapter.locked() as payload:
        payload["pages"] = [invalid]
        store._adapter.replace_all(payload)

    with pytest.raises(PageStoreError, match="unreadable"):
        store.get("run:page")
