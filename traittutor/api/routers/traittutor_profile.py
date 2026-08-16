"""TraitTutor Big Five learner-profile API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from traittutor.assessment.big_five import (
    TIPI_QUESTIONS,
    TIPI_RESPONSE_OPTIONS,
    TRAIT_LABELS,
    TRAIT_ORDER,
    IncompleteTIPIError,
    build_trait_profile,
    delete_trait_profile,
    list_trait_profiles,
    load_trait_profile,
    save_trait_profile,
)

router = APIRouter()


class TraitProfileRequest(BaseModel):
    answers: dict[str, int] = Field(default_factory=dict)
    user_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


@router.get("/questions")
async def get_trait_profile_questions():
    return {
        "instrument": "BFI-10/TIPI",
        "scale": {"min": 1, "max": 5, "neutral": 3},
        "options": list(TIPI_RESPONSE_OPTIONS),
        "questions": list(TIPI_QUESTIONS),
        "traits": [{"key": trait, **TRAIT_LABELS[trait]} for trait in TRAIT_ORDER],
        "usage_boundary": (
            "Trait scores personalize teaching strategy only; they are not a diagnosis, "
            "learning-style label, or learner-ability measure."
        ),
    }


@router.get("/profiles")
async def list_profiles():
    profiles = list_trait_profiles()
    return {"profiles": profiles, "total": len(profiles)}


@router.post("/profiles")
async def create_profile(request: TraitProfileRequest):
    try:
        profile = build_trait_profile(
            request.answers,
            user_id=request.user_id,
            metadata=request.metadata,
        )
        save_trait_profile(profile)
        return profile.to_dict()
    except IncompleteTIPIError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/profiles/{profile_id}")
async def get_profile(profile_id: str):
    try:
        return load_trait_profile(profile_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Trait profile not found") from exc


@router.delete("/profiles/{profile_id}")
async def delete_profile(profile_id: str):
    if not delete_trait_profile(profile_id):
        raise HTTPException(status_code=404, detail="Trait profile not found")
    return {"status": "deleted", "profile_id": profile_id}
