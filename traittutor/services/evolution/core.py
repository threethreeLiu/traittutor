"""Domain model for TraitTutor's evidence-led personalization evolution.

This module deliberately does not expose the legacy three-layer memory model.
It models a short loop instead: immutable evidence becomes a user-governed
reflection, and Hermes compiles approved reflections into a task-local compass.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Mapping
from uuid import uuid4

ReflectionState = Literal["proposed", "confirmed", "rejected", "expired"]
SignalKind = Literal[
    "instruction",
    "preference",
    "goal",
    "quiz_result",
    "strategy_feedback",
    "material_context",
]


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class EvidenceRef:
    """A source that can be opened or audited by the product."""

    ref_id: str
    source: Literal["user", "chat", "notebook", "quiz", "knowledge", "profile"]
    label: str = ""

    def __post_init__(self) -> None:
        if not self.ref_id.strip():
            raise ValueError("evidence reference id cannot be empty")


@dataclass(frozen=True)
class Trail:
    """An append-only learning event; never a personality inference."""

    kind: SignalKind
    payload: dict[str, Any]
    evidence: tuple[EvidenceRef, ...]
    owner_id: str | None = None
    subject_id: str | None = None
    trail_id: str = field(default_factory=lambda: f"tr_{uuid4().hex}")
    occurred_at: str = field(default_factory=_now)

    def __post_init__(self) -> None:
        if not self.evidence:
            raise ValueError("trail requires at least one evidence reference")


@dataclass(frozen=True)
class Reflection:
    """A scoped, explainable observation that a user can govern."""

    statement: str
    category: Literal["preference", "goal", "concept", "strategy"]
    evidence: tuple[EvidenceRef, ...]
    confidence: float
    subject_id: str | None = None
    state: ReflectionState = "proposed"
    reflection_id: str = field(default_factory=lambda: f"rf_{uuid4().hex}")
    expires_at: str | None = None

    def __post_init__(self) -> None:
        if not self.statement.strip():
            raise ValueError("reflection statement cannot be empty")
        if not 0 <= self.confidence <= 1:
            raise ValueError("reflection confidence must be between 0 and 1")
        if not self.evidence:
            raise ValueError("reflection requires at least one evidence reference")
        if self.state not in {"proposed", "confirmed", "rejected", "expired"}:
            raise ValueError("invalid reflection state")


@dataclass(frozen=True)
class Compass:
    """The minimal, versioned personalization input for one task."""

    purpose: str
    strategy: dict[str, Any]
    reflection_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    version: str = field(default_factory=lambda: f"cp_{uuid4().hex[:16]}")
    degraded: bool = False

    def to_prompt_context(self) -> dict[str, Any]:
        return {
            "purpose": self.purpose,
            "strategy": dict(self.strategy),
            "evidence_refs": list(self.evidence_ids),
            "compass_version": self.version,
            "degraded": self.degraded,
            "boundary": "Personalization cues adjust teaching strategy only; they do not diagnose or measure ability.",
        }


class Hermes:
    """Small policy engine for Observe → Reflect → Apply → Learn."""

    def propose(self, trail: Trail) -> Reflection | None:
        """Create only bounded proposals from explicit, useful signals."""
        if trail.kind not in {"instruction", "preference", "goal", "strategy_feedback"}:
            return None
        text = str(trail.payload.get("statement") or trail.payload.get("text") or "").strip()
        if not text:
            return None
        category: Literal["preference", "goal", "concept", "strategy"] = (
            "goal"
            if trail.kind == "goal"
            else "strategy"
            if trail.kind == "strategy_feedback"
            else "preference"
        )
        return Reflection(
            statement=text,
            category=category,
            evidence=trail.evidence,
            confidence=1.0 if trail.kind in {"instruction", "preference", "goal"} else 0.7,
            subject_id=trail.subject_id,
        )

    def apply(
        self,
        *,
        purpose: str,
        reflections: list[Reflection],
        profile: Mapping[str, Any] | None = None,
    ) -> Compass:
        """Compile confirmed reflections and a bounded Big Five cue."""
        confirmed = [r for r in reflections if r.state == "confirmed" and self._is_active(r)]
        strategy: dict[str, Any] = {
            "structure": "stepwise",
            "pacing": "standard",
            "feedback": "direct",
            "active_reflections": [r.statement for r in confirmed[:8]],
        }
        scores = dict((profile or {}).get("scores") or {})
        # Big Five is intentionally a weak fallback, never a user label.
        if scores.get("C", 6) <= 4:
            strategy["structure"] = "stepwise"
        if scores.get("O", 6) >= 8:
            strategy["example_style"] = "exploratory"
        evidence = tuple(ref.ref_id for r in confirmed for ref in r.evidence)
        return Compass(
            purpose=purpose,
            strategy=strategy,
            reflection_ids=tuple(r.reflection_id for r in confirmed),
            evidence_ids=tuple(dict.fromkeys(evidence)),
            degraded=False,
        )

    @staticmethod
    def _is_active(reflection: Reflection) -> bool:
        if not reflection.expires_at:
            return True
        try:
            expiry = datetime.fromisoformat(reflection.expires_at)
        except ValueError:
            return False
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=UTC)
        return expiry > datetime.now(UTC)


def build_compass(
    purpose: str,
    reflections: list[Reflection] | None = None,
    profile: Mapping[str, Any] | None = None,
) -> Compass:
    """Safe entry point: personalization failure returns a usable compass."""
    if not purpose.strip():
        raise ValueError("compass purpose cannot be empty")
    try:
        return Hermes().apply(purpose=purpose, reflections=reflections or [], profile=profile)
    except (TypeError, ValueError, KeyError):
        return Compass(
            purpose=purpose,
            strategy={"structure": "stepwise", "pacing": "standard", "feedback": "direct"},
            reflection_ids=(),
            evidence_ids=(),
            degraded=True,
        )
