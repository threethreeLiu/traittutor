"""TraitTutor Big Five learner-profile API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from traittutor.assessment.big_five import (
    IncompleteTIPIError,
    TIPI_QUESTIONS,
    TIPI_RESPONSE_OPTIONS,
    TRAIT_LABELS,
    TRAIT_ORDER,
    build_trait_profile,
    build_initial_slr_support,
    list_trait_profiles,
    load_trait_profile,
    save_trait_profile,
)
from traittutor.services.path_service import get_path_service

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
        "traits": [
            {"key": trait, **TRAIT_LABELS[trait]}
            for trait in TRAIT_ORDER
        ],
        "usage_boundary": (
            "Trait scores personalize teaching strategy only; they are not a diagnosis, "
            "learning-style label, or learner-ability measure."
        ),
    }


@router.get("/profiles")
async def list_profiles():
    profiles = [_ensure_slr_support(profile) for profile in list_trait_profiles()]
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
        return _ensure_slr_support(load_trait_profile(profile_id))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Trait profile not found") from exc


@router.delete("/profiles/{profile_id}")
async def delete_profile(profile_id: str):
    path = get_path_service().get_workspace_dir() / "traittutor" / "profiles" / f"{profile_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Trait profile not found")
    path.unlink()
    return {"status": "deleted", "profile_id": profile_id}


def _ensure_slr_support(profile: dict[str, Any]) -> dict[str, Any]:
    """Upgrade legacy profile support to the product-owned action catalog."""
    from traittutor.assessment.support_profile import build_slr_action_support

    metadata = dict(profile.get("metadata") or {})
    current = metadata.get("slr_support")
    if not isinstance(current, dict) or current.get("source") == "big_five_initial":
        metadata["slr_support"] = build_slr_action_support(profile.get("scores") or {})
    return {**profile, "metadata": metadata}
