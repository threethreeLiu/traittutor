"""Typed, owner-bound Tutor Persona contracts and persistence."""

from .compiler import TutorPersonaContract, compile_persona
from .context_adapter import TutorPersonaContext, TutorPersonaContextAdapter
from .models import (
    AccessibilityPreferences,
    QuietHours,
    TutorPersonaProfile,
    TutorPersonaSettings,
    default_persona_settings,
)
from .reminders import ReminderEligibility, reminder_eligibility
from .service import TutorPersonaService
from .store import (
    TutorPersonaIdempotencyConflict,
    TutorPersonaStore,
    TutorPersonaStoreError,
    TutorPersonaVersionConflict,
)

__all__ = [
    "AccessibilityPreferences",
    "QuietHours",
    "ReminderEligibility",
    "TutorPersonaContract",
    "TutorPersonaContext",
    "TutorPersonaContextAdapter",
    "TutorPersonaIdempotencyConflict",
    "TutorPersonaProfile",
    "TutorPersonaService",
    "TutorPersonaSettings",
    "TutorPersonaStore",
    "TutorPersonaStoreError",
    "TutorPersonaVersionConflict",
    "compile_persona",
    "default_persona_settings",
    "reminder_eligibility",
]
