from __future__ import annotations

from traittutor.tutor_persona.compiler import compile_persona
from traittutor.tutor_persona.models import TutorPersonaProfile, TutorPersonaSettings


def _profile(**updates: object) -> TutorPersonaProfile:
    settings = TutorPersonaSettings().model_copy(update=updates)
    return TutorPersonaProfile(
        **settings.model_dump(mode="python"),
        persona_id="tp_test",
        owner_id="owner",
        version=3,
        created_at="2026-08-10T00:00:00+00:00",
        updated_at="2026-08-10T00:01:00+00:00",
    )


def _field_paths(value: object, prefix: str = "") -> set[str]:
    if isinstance(value, dict):
        paths: set[str] = set()
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            paths.add(path)
            paths.update(_field_paths(child, path))
        return paths
    return set()


def test_compiler_is_deterministic_and_structured() -> None:
    profile = _profile(
        tone="calm",
        directness="high",
        voice_id="steady",
        address_terms=("name", "you"),
    )

    first = compile_persona(profile)
    second = compile_persona(profile)

    assert first == second
    assert first.profile_version == 3
    assert first.expression.tone == "calm"
    assert first.modality.voice_id == "steady"
    assert first.identity.address_terms == ("name", "you")


def test_compiled_contract_has_no_teaching_or_security_override_surface() -> None:
    payload = compile_persona(_profile()).model_dump(mode="json")
    paths = _field_paths(payload)
    forbidden_tokens = {
        "answer",
        "bkt",
        "correct_rule",
        "grading",
        "instruction",
        "kc",
        "prompt",
        "rubric",
        "security_override",
        "system",
    }

    assert not {path for path in paths if any(token in path.lower() for token in forbidden_tokens)}
