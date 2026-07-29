"""Deterministic policy gate; agents may request but never grant permissions."""

from __future__ import annotations

from .schemas import ToolPolicyDecision


def preflight(message: str) -> list[ToolPolicyDecision]:
    lowered = message.lower()
    decisions: list[ToolPolicyDecision] = []
    if any(token in lowered for token in ("delete ", "remove ", "send ", "publish ", "upload ")):
        decisions.append(ToolPolicyDecision(action="external_side_effect", decision="approval_required", reason="External or destructive actions require confirmation."))
    if any(token in lowered for token in ("/etc/", "~/.ssh", "password", "api key", "secret")):
        decisions.append(ToolPolicyDecision(action="sensitive_access", decision="blocked", reason="Sensitive host paths and credentials are never available to agents."))
    if any(token in lowered for token in ("run code", "execute", "python", "script", "file")):
        decisions.append(ToolPolicyDecision(action="sandbox", decision="allowed", reason="Execution is limited to an isolated per-task sandbox."))
    if not decisions:
        decisions.append(ToolPolicyDecision(action="model_response", decision="allowed", reason="No privileged tool is required."))
    return decisions
