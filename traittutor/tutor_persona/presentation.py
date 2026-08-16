"""Presentation adapters for the versioned Tutor Persona contract.

These adapters deliberately expose only closed, presentation-only fields. They
never carry learner evidence, answers, grading policy, or arbitrary prompt text.
"""

from __future__ import annotations

import os
from typing import Any

from .compiler import TutorPersonaContract


def courseware_presentation(contract: TutorPersonaContract) -> dict[str, Any]:
    """Return the bounded expression payload consumed by courseware prompts."""

    return {
        "contract_version": contract.contract_version,
        "profile_version": contract.profile_version,
        "identity": {
            "display_name": contract.identity.display_name,
            "address_terms": list(contract.identity.address_terms),
        },
        "expression": contract.expression.model_dump(mode="json"),
        "safety_version": contract.safety_version,
    }


def configured_voice_name(contract: TutorPersonaContract) -> str | None:
    """Resolve a product voice alias without guessing a provider-specific name.

    Operators map the closed product aliases to names supported by their active
    TTS provider. An absent mapping leaves the provider's configured default in
    place, while speech rate still follows the same Persona contract.
    """

    alias = contract.modality.voice_id
    if alias == "default":
        return None
    value = os.getenv(f"TRAITTUTOR_PERSONA_VOICE_{alias.upper()}", "").strip()
    return value or None


__all__ = ["configured_voice_name", "courseware_presentation"]
