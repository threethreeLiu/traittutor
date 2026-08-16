from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException
import pytest

from traittutor import learning_packs
from traittutor.api.routers.learning_packs import (
    AppendPackMaterialRequest,
    CreatePackRequest,
    RemovePackMaterialRequest,
    ReorderPackMaterialsRequest,
    append_learning_pack_material,
    create_learning_pack,
    get_learning_pack_material_capabilities,
    remove_learning_pack_material,
    reorder_learning_pack_materials,
)
from traittutor.services.path_service import PathService


@pytest.fixture
def learning_workspace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> PathService:
    service = PathService(workspace_root=tmp_path)
    monkeypatch.setattr(learning_packs, "get_path_service", lambda: service)
    return service


def _material(title: str, text: str) -> dict[str, object]:
    return {
        "source_type": "paste",
        "title": title,
        "text": text,
        "metadata": {"language": "en"},
    }


def test_material_mutations_are_cas_guarded_idempotent_and_recoverable(
    learning_workspace: PathService,
) -> None:
    pack = learning_packs.create_pack(title="Pack", material=_material("First", "alpha"))
    first_id = pack["materials"][0]["material_id"]

    appended, replayed = learning_packs.append_pack_material(
        pack["pack_id"],
        material=_material("Second", "beta"),
        expected_revision=1,
        idempotency_key="append-second",
    )
    assert appended is not None and replayed is False
    assert appended["revision"] == 2
    second_id = appended["material_ids"][1]

    replay, replayed = learning_packs.append_pack_material(
        pack["pack_id"],
        material=_material("Second", "beta"),
        expected_revision=1,
        idempotency_key="append-second",
    )
    assert replay == appended
    assert replayed is True

    with pytest.raises(learning_packs.MaterialIdempotencyConflict):
        learning_packs.append_pack_material(
            pack["pack_id"],
            material=_material("Different", "gamma"),
            expected_revision=1,
            idempotency_key="append-second",
        )
    with pytest.raises(learning_packs.MaterialRevisionConflict) as conflict:
        learning_packs.remove_pack_material(
            pack["pack_id"],
            material_id=first_id,
            expected_revision=1,
            idempotency_key="stale-remove",
        )
    assert conflict.value.actual_revision == 2

    reordered, replayed = learning_packs.reorder_pack_materials(
        pack["pack_id"],
        material_ids=[second_id, first_id],
        expected_revision=2,
        idempotency_key="reorder",
    )
    assert reordered is not None and replayed is False
    assert reordered["revision"] == 3
    assert reordered["material_ids"] == [second_id, first_id]

    removed, replayed = learning_packs.remove_pack_material(
        pack["pack_id"],
        material_id=first_id,
        expected_revision=3,
        idempotency_key="remove-first",
    )
    assert removed is not None and replayed is False
    assert removed["revision"] == 4
    assert removed["material_ids"] == [second_id]

    current = learning_packs.get_pack(pack["pack_id"])
    old = learning_packs.get_pack_material_revision(pack["pack_id"], 2)
    assert current is not None and current["materials"][0]["title"] == "Second"
    assert old is not None and old["material_ids"] == [first_id, second_id]
    assert len(current["material_revisions"]) == 4


@pytest.mark.asyncio
async def test_material_api_maps_conflicts_and_rejects_image_before_write(
    learning_workspace: PathService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TRAITTUTOR_IMAGE_MATERIAL_OCR", raising=False)
    pack = learning_packs.create_pack(title="Pack")
    capabilities = await get_learning_pack_material_capabilities()
    assert capabilities["image_ocr"]["available"] is False
    assert capabilities["image_ocr"]["error_code"] == "image_ocr_unavailable"
    assert capabilities["image_ocr"]["supported_mime_types"] == [
        "image/jpeg",
        "image/png",
        "image/webp",
    ]

    with pytest.raises(HTTPException) as create_image_error:
        await create_learning_pack(
            CreatePackRequest(
                title="Image pack",
                material={
                    "source_type": "image",
                    "title": "Diagram",
                    "source_id": "upload-create",
                },
            )
        )
    assert create_image_error.value.detail["code"] == "invalid_material_operation"
    assert create_image_error.value.detail["message"] == "invalid_image_source_reference"

    with pytest.raises(HTTPException) as image_error:
        await append_learning_pack_material(
            pack["pack_id"],
            AppendPackMaterialRequest(
                expected_revision=0,
                idempotency_key="image-one",
                material={
                    "source_type": "image",
                    "title": "Diagram",
                    "source_id": "upload-1",
                },
            ),
        )
    assert image_error.value.status_code == 422
    assert image_error.value.detail["code"] == "invalid_material_operation"
    assert image_error.value.detail["message"] == "invalid_image_source_reference"
    assert learning_packs.get_pack(pack["pack_id"])["material_revision"] == 0

    appended = await append_learning_pack_material(
        pack["pack_id"],
        AppendPackMaterialRequest(
            expected_revision=0,
            idempotency_key="append-one",
            material=_material("First", "alpha"),
        ),
    )
    material_id = appended["materials"][0]["material_id"]
    assert appended["revision"] == 1
    assert appended["idempotent_replay"] is False

    with pytest.raises(HTTPException) as stale:
        await remove_learning_pack_material(
            pack["pack_id"],
            material_id,
            RemovePackMaterialRequest(expected_revision=0, idempotency_key="remove-stale"),
        )
    assert stale.value.status_code == 409
    assert stale.value.detail == {
        "code": "material_revision_conflict",
        "expected_revision": 0,
        "actual_revision": 1,
    }

    with pytest.raises(HTTPException) as invalid_order:
        await reorder_learning_pack_materials(
            pack["pack_id"],
            ReorderPackMaterialsRequest(
                expected_revision=1,
                idempotency_key="bad-order",
                material_ids=[material_id, material_id],
            ),
        )
    assert invalid_order.value.status_code == 422
    assert invalid_order.value.detail["code"] == "invalid_material_operation"
