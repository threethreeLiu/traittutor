"""Architecture guard for the retired services-layer LLM factory."""

from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
TRAITTUTOR = ROOT / "traittutor"


def test_legacy_llm_factory_is_deleted_and_has_no_runtime_importers() -> None:
    factory_path = TRAITTUTOR / "services" / "llm" / "factory.py"
    assert not factory_path.exists(), "services/llm/factory.py must be retired"

    forbidden = re.compile(
        r"traittutor\.services\.llm\.factory|"
        r"from\s+traittutor\.services\.llm\s+import\s+(?:[^\n]*\b)?(?:complete|stream)\b|"
        r"from\s+\.\s+import\s+factory"
    )
    offenders: list[str] = []
    for path in TRAITTUTOR.rglob("*.py"):
        match = forbidden.search(path.read_text(encoding="utf-8"))
        if match:
            offenders.append(f"{path.relative_to(ROOT)}: {match.group(0)}")

    assert offenders == []


def test_context_explorer_model_calls_are_gateway_owned() -> None:
    source = (TRAITTUTOR / "capabilities" / "explore_context" / "explorer.py").read_text(
        encoding="utf-8"
    )
    assert "build_openai_client" not in source
    assert "get_gateway" in source


def test_agentic_pipelines_have_no_direct_provider_client_factory() -> None:
    runtime_paths = (
        TRAITTUTOR / "agents" / "chat" / "agentic_pipeline.py",
        TRAITTUTOR / "agents" / "chat" / "agent_loop.py",
        TRAITTUTOR / "agents" / "question" / "pipeline.py",
        TRAITTUTOR / "agents" / "research" / "pipeline.py",
        TRAITTUTOR / "core" / "agentic" / "client.py",
        TRAITTUTOR / "core" / "agentic" / "gateway_client.py",
        TRAITTUTOR / "capabilities" / "explore_context" / "explorer.py",
    )
    forbidden = re.compile(
        r"\b(?:build_openai_client|LLMClientConfig|AsyncOpenAI|AsyncAzureOpenAI|"
        r"get_runtime_provider)\b"
    )
    offenders: list[str] = []
    for path in runtime_paths:
        match = forbidden.search(path.read_text(encoding="utf-8"))
        if match:
            offenders.append(f"{path.relative_to(ROOT)}: {match.group(0)}")

    assert offenders == []
