"""Application service for typed Tutor Persona operations."""

from __future__ import annotations

from .compiler import TutorPersonaContract, compile_persona
from .context_adapter import TutorPersonaContext, TutorPersonaContextAdapter
from .models import TutorPersonaProfile, TutorPersonaSettings, default_persona_settings
from .reminders import ReminderEligibility, reminder_eligibility
from .store import TutorPersonaStore


class TutorPersonaService:
    """Thin composition seam for a future owner-derived HTTP router."""

    def __init__(self, store: TutorPersonaStore) -> None:
        self._store = store

    def get_profile(self) -> TutorPersonaProfile:
        return self._store.get_or_create_default()

    def replace_profile(
        self,
        settings: TutorPersonaSettings,
        *,
        expected_version: int,
        idempotency_key: str,
    ) -> TutorPersonaProfile:
        return self._store.update(
            settings,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
        )

    def reset_profile(self, *, expected_version: int, idempotency_key: str) -> TutorPersonaProfile:
        return self.replace_profile(
            default_persona_settings(),
            expected_version=expected_version,
            idempotency_key=idempotency_key,
        )

    def preview(self, profile: TutorPersonaProfile | None = None) -> TutorPersonaContract:
        return compile_persona(profile or self.get_profile())

    def context(self, profile: TutorPersonaProfile | None = None) -> TutorPersonaContext:
        return TutorPersonaContextAdapter.adapt(profile or self.get_profile())

    def reminder_eligibility(
        self, profile: TutorPersonaProfile | None = None
    ) -> ReminderEligibility:
        """Evaluate explicit consent and quiet hours without scheduling a message."""
        return reminder_eligibility(profile or self.get_profile())


__all__ = ["TutorPersonaService"]
