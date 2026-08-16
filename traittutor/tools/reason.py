"""
Reason tool — stateless LLM deep-reasoning call.

When the solver agent needs deeper analysis, logical deduction, or synthesis
of already-gathered information but no external tool (RAG / web / code) is
required, it delegates to this tool.  A single, stateless LLM call produces
a step-by-step reasoning trace that is returned as the observation.

Usage:
    from traittutor.tools.reason import reason

    result = await reason(
        query="Derive the closed-form solution for ...",
        context="Original question: ... \\nPlan: ...",
    )
    print(result["answer"])
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a deep reasoning engine.  You receive a problem context and a \
specific reasoning focus.  Your job is to perform rigorous, step-by-step \
logical analysis and arrive at a clear conclusion.

Guidelines:
- Think carefully and systematically.
- Show your reasoning chain explicitly — number each step.
- If mathematical derivation is needed, show each algebraic step.
- If logical deduction is needed, state premises and inferences clearly.
- Synthesize any provided context but do NOT fabricate facts or cite \
  sources you do not have.
- Conclude with a concise, clearly-labeled answer or conclusion.\
"""


async def reason(
    query: str,
    context: str = "",
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
) -> dict[str, Any]:
    """Perform deep reasoning via a single stateless LLM call.

    Args:
        query:       The reasoning focus — what needs to be analysed or derived.
        context:     Optional surrounding context (original question, plan, prior
                     observations) assembled by the caller.
        api_key:     LLM API key (falls back to global config).
        base_url:    LLM base URL (falls back to global config).
        model:       Model name (falls back to global config).
        max_tokens:  Max output tokens (falls back to global config / agents.yaml).
        temperature: Sampling temperature (falls back to global config / agents.yaml).

    Returns:
        dict with keys ``query``, ``answer``, ``model``.
    """
    from traittutor.gateway import (
        GatewayMessage,
        GatewayRequest,
        gateway_config_with_overrides,
        get_gateway,
    )
    from traittutor.services.config import get_agent_params

    # ---- resolve LLM config ------------------------------------------------
    gateway_config = gateway_config_with_overrides(
        model=model,
        api_key=api_key,
        base_url=base_url,
    )
    model = gateway_config.model

    agent_params = get_agent_params("solve")
    if max_tokens is None:
        max_tokens = agent_params.get("max_tokens", 4096)
    if temperature is None:
        temperature = agent_params.get("temperature", 0.0)

    # ---- build user prompt --------------------------------------------------
    parts: list[str] = []
    if context:
        parts.append(f"## Context\n{context}")
    parts.append(f"## Reasoning Focus\n{query}")
    user_prompt = "\n\n".join(parts)

    # ---- call LLM -----------------------------------------------------------
    logger.debug("reason tool: model=%s, query=%s...", model, query[:80])

    request = GatewayRequest(
        prompt=user_prompt,
        system_prompt=_SYSTEM_PROMPT,
        purpose="tool:reason",
        messages=(
            GatewayMessage(role="system", content=_SYSTEM_PROMPT),
            GatewayMessage(role="user", content=user_prompt),
        ),
        temperature=temperature,
        max_tokens=max_tokens,
        llm_config=gateway_config,
    )
    _chunks: list[str] = []
    async for _c in get_gateway().stream_text(request, include_reasoning=True):
        _chunks.append(_c)
    answer = "".join(_chunks)

    return {
        "query": query,
        "answer": answer.strip(),
        "model": model,
    }
