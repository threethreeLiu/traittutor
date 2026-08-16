"""Deterministically compile a profile into a bounded expression contract."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from .models import (
    AccessibilityPreferences,
    AddressTerm,
    AvatarRef,
    EmojiPolicy,
    FeedbackFormat,
    Intensity,
    Proactivity,
    QuietHours,
    Tone,
    TutorPersonaProfile,
    VoiceId,
)


class PersonaIdentityContract(BaseModel):
    """Presentation identity only; never an authorization or teaching identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    display_name: str
    address_terms: tuple[AddressTerm, ...]
    avatar_ref: AvatarRef


class PersonaExpressionContract(BaseModel):
    """Closed expression choices that cannot encode arbitrary instructions."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tone: Tone
    directness: Intensity
    humor_level: Intensity
    encouragement_level: Intensity
    feedback_format: FeedbackFormat
    proactivity: Proactivity
    emoji_policy: EmojiPolicy


class PersonaModalityContract(BaseModel):
    """Voice and accessibility presentation settings."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    voice_id: VoiceId
    speech_rate: float
    accessibility: AccessibilityPreferences


class TutorPersonaContract(BaseModel):
    """A versioned style-only output kept separate from teaching context."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_version: Literal["tutor-persona-contract.v1"] = "tutor-persona-contract.v1"
    persona_id: str
    profile_version: int
    identity: PersonaIdentityContract
    expression: PersonaExpressionContract
    modality: PersonaModalityContract
    quiet_hours: QuietHours
    safety_version: Literal["persona-safety-v1"]


def compile_persona(profile: TutorPersonaProfile) -> TutorPersonaContract:
    """Compile *profile* without prompts, model calls, or mutable state reads."""

    return TutorPersonaContract(
        persona_id=profile.persona_id,
        profile_version=profile.version,
        identity=PersonaIdentityContract(
            display_name=profile.name,
            address_terms=profile.address_terms,
            avatar_ref=profile.avatar_ref,
        ),
        expression=PersonaExpressionContract(
            tone=profile.tone,
            directness=profile.directness,
            humor_level=profile.humor_level,
            encouragement_level=profile.encouragement_level,
            feedback_format=profile.feedback_format,
            proactivity=profile.proactivity,
            emoji_policy=profile.emoji_policy,
        ),
        modality=PersonaModalityContract(
            voice_id=profile.voice_id,
            speech_rate=profile.speech_rate,
            accessibility=profile.accessibility,
        ),
        quiet_hours=profile.quiet_hours,
        safety_version=profile.safety_version,
    )


__all__ = [
    "PersonaExpressionContract",
    "PersonaIdentityContract",
    "PersonaModalityContract",
    "TutorPersonaContract",
    "compile_persona",
]
