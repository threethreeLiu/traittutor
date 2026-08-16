"""
Brainstorm tool - stateless breadth-first idea exploration.

This tool performs a single LLM call to explore multiple plausible directions
for a topic and briefly justify each one.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a breadth-first brainstorming engine.

Given a topic and optional supporting context, explore multiple promising
directions instead of converging too early on one answer.

Requirements:
- Generate 5-8 distinct possibilities from different angles when possible.
- Keep each possibility concrete and easy to scan.
- For each possibility, include a short rationale explaining why it is worth exploring.
- Prefer variety: methods, framing, applications, risks, experiments, or product directions.
- Do not pretend uncertain facts are verified.
- Keep the response concise, structured, and actionable.

Output in Markdown using this structure:

# Brainstorm

## 1. <short title>
- Direction: <1-2 sentence idea>
- Rationale: <brief why>

## 2. <short title>
- Direction: <1-2 sentence idea>
- Rationale: <brief why>

Continue for the remaining ideas.
"""


async def brainstorm(
    topic: str,
    context: str = "",
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
) -> dict[str, Any]:
    """Generate breadth-first ideas for a topic via one LLM call."""
    from traittutor.gateway import (
        GatewayMessage,
        GatewayRequest,
        gateway_config_with_overrides,
        get_gateway,
    )
    from traittutor.services.config import get_agent_params

    gateway_config = gateway_config_with_overrides(
        model=model,
        api_key=api_key,
        base_url=base_url,
    )
    model = gateway_config.model

    agent_params = get_agent_params("brainstorm")
    if max_tokens is None:
        max_tokens = agent_params.get("max_tokens", 2048)
    if temperature is None:
        temperature = agent_params.get("temperature", 0.8)

    parts: list[str] = [f"## Topic\n{topic.strip()}"]
    if context and context.strip():
        parts.append(f"## Context\n{context.strip()}")
    user_prompt = "\n\n".join(parts)

    logger.debug("brainstorm tool: model=%s, topic=%s...", model, topic[:80])

    request = GatewayRequest(
        prompt=user_prompt,
        system_prompt=_SYSTEM_PROMPT,
        purpose="tool:brainstorm",
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
        "topic": topic,
        "answer": answer.strip(),
        "model": model,
    }
