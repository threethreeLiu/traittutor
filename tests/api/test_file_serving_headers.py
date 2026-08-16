"""Same-origin file serving must neutralize active content (stored XSS).

The web proxy serves ``/api/*`` same-origin, so an uploaded or model-generated
HTML/SVG artifact rendered inline would script in the application origin with
the user's cookies. These tests pin the header contract: active content is
forced to a neutral download, and every served artifact carries a sandbox CSP.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from traittutor.api.routers import attachments as attachments_router
from traittutor.api.routers import outputs as outputs_router
from traittutor.api.utils.file_serving import (
    content_disposition,
    forced_download_media_type,
    is_active_content,
)
from traittutor.services.path_service import PathService
from traittutor.services.storage import LocalDiskAttachmentStore


def test_active_content_detection() -> None:
    assert is_active_content("report.html")
    assert is_active_content("Vector.SVG")
    assert is_active_content("a/b.xhtml")
    assert not is_active_content("photo.png")
    assert not is_active_content("doc.pdf")
    assert forced_download_media_type("x.svg") == "application/octet-stream"
    assert forced_download_media_type("x.png") == ""


def test_content_disposition_handles_non_ascii() -> None:
    header = content_disposition("学习计划.html", disposition="attachment")
    assert header.startswith("attachment; filename=")
    assert "filename*=UTF-8''" in header
    assert "%E5%AD%A6" in header  # percent-encoded UTF-8, no latin-1 crash


@dataclass
class _Bundle:
    client: TestClient
    store: LocalDiskAttachmentStore
    outputs_service: PathService


class _StubSessionStore:
    """Owner-scoped session lookup stub: 's1' exists, anything else misses."""

    async def get_session(self, session_id: str):
        return {"id": session_id} if session_id == "s1" else None


@pytest.fixture
def bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Bundle:
    store = LocalDiskAttachmentStore(root=tmp_path / "attachments")
    monkeypatch.setattr(attachments_router, "get_attachment_store", lambda: store)
    monkeypatch.setattr(attachments_router, "get_session_store", lambda: _StubSessionStore())

    outputs_service = PathService(workspace_root=tmp_path / "ws")
    monkeypatch.setattr(outputs_router, "get_path_service", lambda: outputs_service)

    app = FastAPI()
    app.include_router(attachments_router.router, prefix="/attachments")
    app.include_router(outputs_router.router, prefix="/outputs")
    return _Bundle(client=TestClient(app), store=store, outputs_service=outputs_service)


def _seed_attachment(bundle: _Bundle, filename: str) -> None:
    session_dir = bundle.store.root / "s1"
    session_dir.mkdir(parents=True, exist_ok=True)
    stored = bundle.store._stored_filename("att1", filename)
    (session_dir / stored).write_bytes(b"payload")


def _seed_output(bundle: _Bundle, relative: str) -> None:
    target = bundle.outputs_service.get_public_outputs_root() / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"payload")


def test_attachment_html_is_forced_download_and_sandboxed(bundle: _Bundle) -> None:
    _seed_attachment(bundle, "evil.html")
    response = bundle.client.get("/attachments/s1/att1/evil.html")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/octet-stream")
    assert response.headers["content-disposition"].startswith("attachment")
    assert "sandbox" in response.headers.get("content-security-policy", "")
    assert response.headers.get("x-content-type-options") == "nosniff"


def test_attachment_foreign_session_404s_even_when_file_exists(bundle: _Bundle) -> None:
    # Seed the file under another user's session directory: the store would
    # happily resolve it (shared root), but the owner-scoped session lookup
    # must refuse before any byte is served.
    _seed_attachment(bundle, "private.png")
    foreign = bundle.store.root / "someoneelse"
    foreign.mkdir(parents=True, exist_ok=True)
    stored = bundle.store._stored_filename("att1", "private.png")
    (foreign / stored).write_bytes(b"payload")
    response = bundle.client.get("/attachments/someoneelse/att1/private.png")
    assert response.status_code == 404
    assert b"payload" not in response.content


def test_attachment_image_stays_inline_but_sandboxed(bundle: _Bundle) -> None:
    _seed_attachment(bundle, "diagram.png")
    response = bundle.client.get("/attachments/s1/att1/diagram.png")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/png")
    assert response.headers["content-disposition"].startswith("inline")
    assert "sandbox" in response.headers.get("content-security-policy", "")


def test_output_active_content_is_forced_download(bundle: _Bundle) -> None:
    _seed_output(bundle, "workspace/chat/_detached_code_execution/run1/index.html")
    response = bundle.client.get("/outputs/workspace/chat/_detached_code_execution/run1/index.html")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/octet-stream")
    assert response.headers["content-disposition"].startswith("attachment")
    assert "sandbox" in response.headers.get("content-security-policy", "")


def test_output_passive_media_serves_sandboxed(bundle: _Bundle) -> None:
    _seed_output(bundle, "workspace/chat/_detached_code_execution/run2/frame.png")
    response = bundle.client.get("/outputs/workspace/chat/_detached_code_execution/run2/frame.png")
    assert response.status_code == 200
    assert "sandbox" in response.headers.get("content-security-policy", "")
    assert "attachment" not in response.headers.get("content-disposition", "")
