from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import HTTPException
import pytest

from traittutor import learning_packs
from traittutor.api.routers.traittutor_generate import PrepareMaterialRequest, prepare_material
from traittutor.generate import image_material
from traittutor.services.llm.config import LLMConfig
from traittutor.services.path_service import PathService


class FakeGateway:
    def __init__(
        self, content: str = "Newton's second law: F = ma", error: Exception | None = None
    ):
        self.content = content
        self.error = error
        self.requests: list[Any] = []

    async def complete(self, request: Any) -> Any:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(content=self.content)


class FakeAttachmentStore:
    def __init__(self) -> None:
        self.puts: list[dict[str, Any]] = []

    async def put(self, **kwargs: Any) -> str:
        self.puts.append(kwargs)
        return (
            f"/api/attachments/{kwargs['session_id']}/"
            f"{kwargs['attachment_id']}/{kwargs['filename']}"
        )


@pytest.fixture
def image_workspace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> PathService:
    service = PathService(workspace_root=tmp_path)
    monkeypatch.setattr(image_material, "get_path_service", lambda: service)
    return service


def _vision_config() -> LLMConfig:
    return LLMConfig(
        model="gpt-4o",
        api_key="test-only",
        base_url="https://gateway.invalid/v1",
        binding="openai",
        provider_name="openai",
    )


def test_capability_requires_flag_credentials_and_vision_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(image_material.IMAGE_MATERIAL_OCR_FLAG, "1")
    assert image_material.image_ocr_capability(_vision_config())["available"] is True

    missing_credentials = _vision_config().model_copy({"api_key": ""})
    assert image_material.image_ocr_capability(missing_credentials)["available"] is False

    text_only = _vision_config().model_copy({"model": "gpt-3.5-turbo"})
    assert image_material.image_ocr_capability(text_only)["available"] is False


@pytest.mark.parametrize(
    ("filename", "mime_type", "data"),
    [
        ("notes.jpg", "image/jpeg", b"\xff\xd8\xff\xe0test-jpeg"),
        ("notes.png", "image/png", b"\x89PNG\r\n\x1a\ntest-png"),
        ("notes.webp", "image/webp", b"RIFF\x08\x00\x00\x00WEBPtest-webp"),
    ],
)
@pytest.mark.asyncio
async def test_supported_images_use_gateway_then_persist_owner_bound_source(
    monkeypatch: pytest.MonkeyPatch,
    image_workspace: PathService,
    filename: str,
    mime_type: str,
    data: bytes,
) -> None:
    monkeypatch.setenv(image_material.IMAGE_MATERIAL_OCR_FLAG, "1")
    gateway = FakeGateway()
    store = FakeAttachmentStore()

    material = await image_material.prepare_learning_image(
        filename,
        data,
        mime_type=mime_type,
        owner_id="learner-a",
        gateway=gateway,  # type: ignore[arg-type]
        attachment_store=store,  # type: ignore[arg-type]
        llm_config=_vision_config(),
    )

    assert material["source_type"] == "upload"
    assert material["source_id"].startswith("image-")
    assert material["metadata"]["content_hash"] == hashlib.sha256(data).hexdigest()
    assert material["metadata"]["extracted_text"] == "Newton's second law: F = ma"
    assert material["metadata"]["mime_type"] == mime_type
    assert len(gateway.requests) == 1
    assert gateway.requests[0].purpose == "learning_material_image_ocr"
    assert gateway.requests[0].user_id == "learner-a"
    assert gateway.requests[0].attachments[0].base64
    assert len(store.puts) == 1

    tampered = {
        **material,
        "title": "tampered",
        "metadata": {**material["metadata"], "extracted_text": "forged"},
    }
    canonical = image_material.canonical_prepared_image_material(
        tampered,
        owner_id="learner-a",
    )
    assert canonical["title"] == filename
    assert canonical["metadata"]["extracted_text"] == "Newton's second law: F = ma"
    with pytest.raises(image_material.LearningImageError) as cross_owner:
        image_material.canonical_prepared_image_material(tampered, owner_id="learner-b")
    assert cross_owner.value.code == "invalid_image_source_reference"


@pytest.mark.asyncio
async def test_unavailable_gate_and_provider_failure_create_no_source(
    monkeypatch: pytest.MonkeyPatch,
    image_workspace: PathService,
) -> None:
    data = b"\x89PNG\r\n\x1a\ncontent"
    gateway = FakeGateway()
    store = FakeAttachmentStore()
    monkeypatch.delenv(image_material.IMAGE_MATERIAL_OCR_FLAG, raising=False)

    with pytest.raises(image_material.LearningImageUnavailable) as unavailable:
        await image_material.prepare_learning_image(
            "notes.png",
            data,
            mime_type="image/png",
            owner_id="learner-a",
            gateway=gateway,  # type: ignore[arg-type]
            attachment_store=store,  # type: ignore[arg-type]
            llm_config=_vision_config(),
        )
    assert unavailable.value.code == "image_ocr_unavailable"
    assert gateway.requests == []
    assert store.puts == []

    monkeypatch.setenv(image_material.IMAGE_MATERIAL_OCR_FLAG, "1")
    failed_gateway = FakeGateway(error=RuntimeError("provider unavailable"))
    with pytest.raises(image_material.LearningImageError) as failed:
        await image_material.prepare_learning_image(
            "notes.png",
            data,
            mime_type="image/png",
            owner_id="learner-a",
            gateway=failed_gateway,  # type: ignore[arg-type]
            attachment_store=store,  # type: ignore[arg-type]
            llm_config=_vision_config(),
        )
    assert failed.value.code == "image_ocr_failed"
    assert store.puts == []
    assert not (
        image_workspace.get_workspace_dir() / "traittutor" / "image-material-sources"
    ).exists()


@pytest.mark.asyncio
async def test_prepare_api_returns_structured_unavailable_without_provider_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(image_material.IMAGE_MATERIAL_OCR_FLAG, raising=False)
    request = PrepareMaterialRequest(
        filename="notes.png",
        mime_type="image/png",
        base64="iVBORw0KGgo=",
    )

    with pytest.raises(HTTPException) as unavailable:
        await prepare_material(request)

    assert unavailable.value.status_code == 503
    assert unavailable.value.detail == {
        "code": "image_ocr_unavailable",
        "message": "Image text extraction is unavailable on this server",
        "capability": "image_ocr",
    }


@pytest.mark.asyncio
async def test_prepared_image_append_uses_private_record_not_client_ocr(
    monkeypatch: pytest.MonkeyPatch,
    image_workspace: PathService,
) -> None:
    monkeypatch.setenv(image_material.IMAGE_MATERIAL_OCR_FLAG, "1")
    monkeypatch.setattr(learning_packs, "get_path_service", lambda: image_workspace)
    material = await image_material.prepare_learning_image(
        "notes.png",
        b"\x89PNG\r\n\x1a\ncontent",
        mime_type="image/png",
        owner_id="local-admin",
        gateway=FakeGateway(content="trusted transcription"),  # type: ignore[arg-type]
        attachment_store=FakeAttachmentStore(),  # type: ignore[arg-type]
        llm_config=_vision_config(),
    )
    pack = learning_packs.create_pack(title="Images")
    material["metadata"]["extracted_text"] = "client-forged text"

    revision, replayed = learning_packs.append_pack_material(
        pack["pack_id"],
        material=material,
        expected_revision=0,
        idempotency_key="append-image",
    )

    assert replayed is False
    assert revision["materials"][0]["source_type"] == "upload"
    assert revision["materials"][0]["metadata"]["source_kind"] == "image"
    assert revision["materials"][0]["metadata"]["extracted_text"] == "trusted transcription"


@pytest.mark.asyncio
async def test_mime_magic_size_and_empty_text_fail_truthfully_before_persistence(
    monkeypatch: pytest.MonkeyPatch,
    image_workspace: PathService,
) -> None:
    monkeypatch.setenv(image_material.IMAGE_MATERIAL_OCR_FLAG, "1")
    store = FakeAttachmentStore()

    with pytest.raises(image_material.LearningImageError) as mismatch:
        await image_material.prepare_learning_image(
            "notes.png",
            b"\xff\xd8\xffbad",
            mime_type="image/png",
            owner_id="learner-a",
            gateway=FakeGateway(),  # type: ignore[arg-type]
            attachment_store=store,  # type: ignore[arg-type]
            llm_config=_vision_config(),
        )
    assert mismatch.value.code == "invalid_image_material"

    with pytest.raises(image_material.LearningImageError) as oversized:
        await image_material.prepare_learning_image(
            "notes.webp",
            b"RIFF\x00\x00\x00\x00WEBP" + b"x" * image_material.MAX_IMAGE_BYTES,
            mime_type="image/webp",
            owner_id="learner-a",
            gateway=FakeGateway(),  # type: ignore[arg-type]
            attachment_store=store,  # type: ignore[arg-type]
            llm_config=_vision_config(),
        )
    assert oversized.value.code == "invalid_image_material"

    with pytest.raises(image_material.LearningImageError) as no_text:
        await image_material.prepare_learning_image(
            "notes.png",
            b"\x89PNG\r\n\x1a\ncontent",
            mime_type="image/png",
            owner_id="learner-a",
            gateway=FakeGateway(content="  "),  # type: ignore[arg-type]
            attachment_store=store,  # type: ignore[arg-type]
            llm_config=_vision_config(),
        )
    assert no_text.value.code == "image_ocr_no_text"
    assert store.puts == []
