"""Deterministic preflight for the canonical chat capability."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ToolPolicyDecision(BaseModel):
    """One server-authored policy decision applied before any Gateway call."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action: str
    decision: str
    reason: str


def preflight(message: str) -> list[ToolPolicyDecision]:
    """Classify privileged-action constraints without invoking a model."""

    lowered = message.lower()
    decisions: list[ToolPolicyDecision] = []
    if any(token in lowered for token in ("delete ", "remove ", "send ", "publish ", "upload ")):
        decisions.append(
            ToolPolicyDecision(
                action="external_side_effect",
                decision="approval_required",
                reason="External or destructive actions require confirmation.",
            )
        )
    if any(token in lowered for token in ("/etc/", "~/.ssh", "password", "api key", "secret")):
        decisions.append(
            ToolPolicyDecision(
                action="sensitive_access",
                decision="blocked",
                reason="Sensitive host paths and credentials are never available to agents.",
            )
        )
    if any(token in lowered for token in ("run code", "execute", "python", "script", "file")):
        decisions.append(
            ToolPolicyDecision(
                action="sandbox",
                decision="allowed",
                reason="Execution is limited to an isolated per-task sandbox.",
            )
        )
    if not decisions:
        decisions.append(
            ToolPolicyDecision(
                action="model_response",
                decision="allowed",
                reason="No privileged tool is required.",
            )
        )
    return decisions


def policy_preflight_contract(decisions: list[ToolPolicyDecision]) -> str:
    """Render the same compact policy boundary used by the retired runtime."""

    policy_text = "; ".join(f"{item.action}:{item.decision}" for item in decisions)
    return f"Policy preflight: {policy_text}."


__all__ = ["ToolPolicyDecision", "policy_preflight_contract", "preflight"]
