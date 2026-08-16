from __future__ import annotations

from copy import deepcopy

from traittutor.tutor_persona.compiler import compile_persona
from traittutor.tutor_persona.context_adapter import TutorPersonaContextAdapter
from traittutor.tutor_persona.models import TutorPersonaProfile, TutorPersonaSettings


def _profile(settings: TutorPersonaSettings, *, version: int) -> TutorPersonaProfile:
    return TutorPersonaProfile(
        **settings.model_dump(mode="python"),
        persona_id="tp_test",
        owner_id="owner",
        version=version,
        created_at="2026-08-10T00:00:00+00:00",
        updated_at=f"2026-08-10T00:0{version}:00+00:00",
    )


def _changed_paths(left: object, right: object, prefix: str = "") -> set[str]:
    if isinstance(left, dict) and isinstance(right, dict):
        changed: set[str] = set()
        for key in left.keys() | right.keys():
            path = f"{prefix}.{key}" if prefix else str(key)
            if key not in left or key not in right:
                changed.add(path)
            else:
                changed.update(_changed_paths(left[key], right[key], path))
        return changed
    return {prefix} if left != right else set()


def test_profile_change_only_changes_expression_and_modality_contract() -> None:
    teaching_inputs = {
        "answer_key_ref": "server-only-answer-v7",
        "rubric_ref": "rubric-v2",
        "kc_id": "kc-calculus-1",
        "bkt_state_version": "bkt-v11",
        "safety_policy_version": "safety-v9",
    }
    baseline_teaching_inputs = deepcopy(teaching_inputs)
    baseline = _profile(TutorPersonaSettings(), version=1)
    changed = _profile(
        TutorPersonaSettings(
            tone="energetic",
            directness="high",
            voice_id="bright",
            speech_rate=1.2,
            emoji_policy="moderate",
        ),
        version=2,
    )

    left = compile_persona(baseline).model_dump(mode="json")
    right = compile_persona(changed).model_dump(mode="json")

    assert teaching_inputs == baseline_teaching_inputs
    assert _changed_paths(left, right) == {
        "profile_version",
        "expression.tone",
        "expression.directness",
        "expression.emoji_policy",
        "modality.voice_id",
        "modality.speech_rate",
    }


def test_context_adapter_is_a_separate_hashed_style_attachment() -> None:
    profile = _profile(TutorPersonaSettings(tone="calm"), version=1)

    first = TutorPersonaContextAdapter.adapt(profile)
    second = TutorPersonaContextAdapter.adapt(profile)

    assert first == second
    assert first.kind == "tutor_persona"
    assert first.profile_ref == "tp_test:v1"
    assert len(first.contract_hash) == 64
    assert set(first.model_dump(mode="json")) == {
        "kind",
        "profile_ref",
        "contract_hash",
        "contract",
    }
