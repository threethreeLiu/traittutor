"""Learning-pack APIs shared by courseware, card, and quiz pages."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from traittutor import learning_packs

router = APIRouter()


class CreatePackRequest(BaseModel):
    title: str = ""
    material: dict[str, Any] = Field(default_factory=dict)
    profile_id: str | None = None


class UpdatePackRequest(BaseModel):
    title: str | None = None
    persona: str | None = None
    profile_id: str | None = None
    artifact: dict[str, Any] | None = None
    flashcard_progress: dict[str, Any] | None = None
    quiz_attempt: dict[str, Any] | None = None


@router.get("")
async def list_learning_packs():
    packs = learning_packs.list_packs()
    return {"packs": packs, "total": len(packs)}


@router.post("")
async def create_learning_pack(request: CreatePackRequest):
    return learning_packs.create_pack(title=request.title, material=request.material, profile_id=request.profile_id)


@router.get("/{pack_id}")
async def get_learning_pack(pack_id: str):
    pack = learning_packs.get_pack(pack_id)
    if pack is None:
        raise HTTPException(status_code=404, detail="Learning pack not found")
    return pack


@router.patch("/{pack_id}")
async def update_learning_pack(pack_id: str, request: UpdatePackRequest):
    patch = request.model_dump(exclude_none=True)
    pack = learning_packs.update_pack(pack_id, patch)
    if pack is None:
        raise HTTPException(status_code=404, detail="Learning pack not found")
    return pack
