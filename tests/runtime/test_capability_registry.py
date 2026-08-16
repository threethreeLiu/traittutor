from __future__ import annotations

from traittutor.runtime.bootstrap.builtin_capabilities import BUILTIN_CAPABILITY_CLASSES
from traittutor.runtime.registry.capability_registry import CapabilityRegistry


def test_current_builtin_capabilities_load_with_matching_manifest_names() -> None:
    """Every capability exposed by the current runtime must load successfully."""

    assert set(BUILTIN_CAPABILITY_CLASSES) == {
        "chat",
        "deep_solve",
        "learning_exploration",
        "knowledge_diagram",
        "humanizer",
        "deep_research",
        "mastery_path",
    }

    registry = CapabilityRegistry()
    registry.load_builtins()

    assert set(registry.list_capabilities()) == set(BUILTIN_CAPABILITY_CLASSES)
    for name in BUILTIN_CAPABILITY_CLASSES:
        capability = registry.get(name)
        assert capability is not None
        assert capability.manifest.name == name
