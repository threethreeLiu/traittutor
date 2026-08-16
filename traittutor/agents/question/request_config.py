"""Runtime config builder for ``QuestionPipeline``.

Mirrors the shape used by :mod:`traittutor.agents.research.request_config`,
but with a much smaller surface — the question pipeline only needs a
handful of knobs out of the service config:

* ``exploring.max_iterations`` (int, default 8) — agentic-loop cap for the
  Explore phase.
* ``exploring.tool_summarizer.enabled`` (bool, default True) — toggle the
  per-tool-result LLM reflection step that compresses raw tool output
  before downstream phases see it.
* ``exploring.tool_summarizer.max_tokens`` (int, default 800) — token cap
  on each summarizer call.

The helper is intentionally tolerant: missing keys / wrong types collapse
to defaults so callers can pass any base config (e.g. ``main.yaml``)
without first defining a ``capabilities.deep_question`` section.
"""

from __future__ import annotations

from typing import Any


def _read_int(source: dict[str, Any], key: str, default: int) -> int:
    value = source.get(key)
    if isinstance(value, int) and value > 0:
        return value
    return default


def _read_bool(source: dict[str, Any], key: str, default: bool) -> bool:
    value = source.get(key)
    if isinstance(value, bool):
        return value
    return default


def _dict_field(source: dict[str, Any], key: str) -> dict[str, Any]:
    value = source.get(key)
    return value if isinstance(value, dict) else {}


def build_question_runtime_config(
    *,
    base_config: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build the runtime_config dict passed to :class:`QuestionPipeline`.

    The pipeline reads its knobs from ``runtime_config["exploring"]`` —
    everything else in ``base_config`` is currently ignored by question.
    """
    base = base_config if isinstance(base_config, dict) else {}
    capabilities = _dict_field(base, "capabilities")
    question_root = _dict_field(capabilities, "deep_question")
    exploring_root = _dict_field(question_root, "exploring")
    summarizer_root = _dict_field(exploring_root, "tool_summarizer")

    exploring = {
        "max_iterations": _read_int(exploring_root, "max_iterations", 8),
        "tool_summarizer": {
            "enabled": _read_bool(summarizer_root, "enabled", True),
            "max_tokens": _read_int(summarizer_root, "max_tokens", 800),
        },
    }

    runtime_config = dict(base)
    runtime_config["exploring"] = exploring
    return runtime_config


__all__ = ["build_question_runtime_config"]
