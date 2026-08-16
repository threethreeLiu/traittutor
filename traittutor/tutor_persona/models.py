"""Frozen whitelist models for user-selected Tutor Persona expression."""

from __future__ import annotations

from datetime import UTC, datetime
import re
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

AddressTerm = Literal["name", "you", "learner", "classmate"]
AvatarRef = Literal["default", "mentor", "guide", "study_buddy"]
VoiceId = Literal["default", "calm", "bright", "steady"]
Tone = Literal["warm", "neutral", "energetic", "calm"]
Intensity = Literal["low", "medium", "high"]
FeedbackFormat = Literal["concise", "balanced", "detailed", "socratic"]
Proactivity = Literal["off", "reminders_only", "moderate"]
EmojiPolicy = Literal["none", "minimal", "moderate"]
TextScale = Literal["standard", "large", "extra_large"]

_DISPLAY_NAME_PATTERN = re.compile(r"^[\w .·-]+$", flags=re.UNICODE)
_LOCAL_TIME_PATTERN = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def _require_utc_iso(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("timestamp must be valid ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ValueError("timestamp must include a UTC offset")
    return value


class QuietHours(BaseModel):
    """A typed notification schedule; it is never rendered into model instructions."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    start_local: str = "22:00"
    end_local: str = "08:00"
    timezone: str = "UTC"

    @field_validator("start_local", "end_local")
    @classmethod
    def _validate_local_time(cls, value: str) -> str:
        if not _LOCAL_TIME_PATTERN.fullmatch(value):
            raise ValueError("quiet-hour time must use HH:MM")
        return value

    @field_validator("timezone")
    @classmethod
    def _validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        return value


class AccessibilityPreferences(BaseModel):
    """Presentation-only accessibility preferences shared across modalities."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    captions: bool = True
    reduced_motion: bool = False
    screen_reader_optimized: bool = False
    text_scale: TextScale = "standard"


class TutorPersonaSettings(BaseModel):
    """The complete user-editable whitelist.

    There is intentionally no arbitrary instruction body. ``name`` is a short
    presentation label with a restricted character set, while address terms
    and every behavioral option are closed enums.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(default="TraitTutor", min_length=1, max_length=40)
    address_terms: tuple[AddressTerm, ...] = Field(default=("you",), min_length=1, max_length=4)
    avatar_ref: AvatarRef = "default"
    voice_id: VoiceId = "default"
    speech_rate: float = Field(default=1.0, ge=0.75, le=1.5)
    tone: Tone = "warm"
    directness: Intensity = "medium"
    humor_level: Intensity = "low"
    encouragement_level: Intensity = "medium"
    feedback_format: FeedbackFormat = "balanced"
    proactivity: Proactivity = "off"
    # Consent is distinct from the presentation preference. A future delivery
    # worker must require both this explicit authorization and a non-off mode.
    reminder_consent: bool = False
    emoji_policy: EmojiPolicy = "minimal"
    quiet_hours: QuietHours = Field(default_factory=QuietHours)
    accessibility: AccessibilityPreferences = Field(default_factory=AccessibilityPreferences)
    safety_version: Literal["persona-safety-v1"] = "persona-safety-v1"

    @field_validator("name")
    @classmethod
    def _validate_display_name(cls, value: str) -> str:
        normalized = value.strip()
        if not _DISPLAY_NAME_PATTERN.fullmatch(normalized):
            raise ValueError(
                "name may contain only letters, numbers, spaces, dot, hyphen, or middle dot"
            )
        return normalized

    @model_validator(mode="after")
    def _unique_address_terms(self) -> TutorPersonaSettings:
        if len(set(self.address_terms)) != len(self.address_terms):
            raise ValueError("address_terms must be unique")
        return self


class TutorPersonaProfile(TutorPersonaSettings):
    """One immutable version of an owner's active Tutor Persona profile."""

    schema_version: Literal["tutor-persona-profile.v1"] = "tutor-persona-profile.v1"
    persona_id: str = Field(min_length=1, max_length=96)
    owner_id: str = Field(min_length=1, max_length=128)
    version: int = Field(ge=1)
    created_at: str
    updated_at: str

    _validate_created_at = field_validator("created_at")(_require_utc_iso)
    _validate_updated_at = field_validator("updated_at")(_require_utc_iso)


def default_persona_settings() -> TutorPersonaSettings:
    """Return the stable product default without owner or persistence data."""

    return TutorPersonaSettings()


__all__ = [
    "AccessibilityPreferences",
    "AddressTerm",
    "AvatarRef",
    "EmojiPolicy",
    "FeedbackFormat",
    "Intensity",
    "Proactivity",
    "QuietHours",
    "TextScale",
    "Tone",
    "TutorPersonaProfile",
    "TutorPersonaSettings",
    "VoiceId",
    "default_persona_settings",
]
