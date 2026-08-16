from __future__ import annotations

from pathlib import Path

from traittutor.tutor_persona.models import TutorPersonaSettings
from traittutor.tutor_persona.service import TutorPersonaService
from traittutor.tutor_persona.store import TutorPersonaStore


def test_service_replace_preview_context_and_reset(tmp_path: Path) -> None:
    service = TutorPersonaService(
        TutorPersonaStore("authenticated-owner", path=tmp_path / "personas.json")
    )
    initial = service.get_profile()

    changed = service.replace_profile(
        TutorPersonaSettings(tone="calm", voice_id="steady"),
        expected_version=initial.version,
        idempotency_key="change-style",
    )
    preview = service.preview(changed)
    context = service.context(changed)

    assert preview.expression.tone == "calm"
    assert preview.modality.voice_id == "steady"
    assert context.contract == preview
    assert context.profile_ref == f"{changed.persona_id}:v{changed.version}"

    reset = service.reset_profile(
        expected_version=changed.version,
        idempotency_key="reset-style",
    )
    assert reset.version == changed.version + 1
    assert reset.tone == "warm"
    assert reset.voice_id == "default"
