"""Read-time forgetting projection (decay-on-read) for mastery signals.

The BKT posterior moves only when a new answer arrives; time itself never
changes it.  This module adds the missing time dimension as a *derived read
projection*: effective mastery decays exponentially back toward the initial
prior with a knowledge-type-specific half-life, so "the learner practised X
six months ago" finally has a model-layer basis.

Design contract:

* **No writes.** The projection is deterministic given (prior, posterior,
  half-life, days since last practice).  It never touches the event ledger,
  stored BKT parameters, or the persisted ``ConceptSignal`` — every stored
  state stays rebuildable (invariant: events first, derived state rebuilt).
* **Calibrated only.** Uncalibrated units already display as "insufficient
  evidence"; decaying them would manufacture pseudo-precise numbers (PRD
  §11).  Projection applies only to calibrated units with at least
  ``MIN_OBSERVATIONS_FOR_PROBABILITY`` verified observations.
* **Recent failures win.** A unit whose persisted ``support_level`` is
  ``needs_support`` (recent wrong answer) is never decayed upward.
* **Where it plugs in.** Project the *read* copies consumed by prompts, the
  component selector, and learner-safe qualitative display policies. Public
  projections expose only the resulting state label, never this private
  effective posterior.
"""

from __future__ import annotations

from datetime import UTC, datetime
import math
from typing import Any, Literal

# Knowledge-type half-lives mirror the spaced-repetition cadence of
# ``learning.scheduler.INTERVAL_SEQUENCES``: memory decays fastest, design
# slowest.  Values are days until half of the mastery-above-prior is lost.
# Wiring status: callers may pass ``knowledge_type`` explicitly, and
# ``project_concept_signal`` falls back to ``signal.knowledge_type`` when the
# signal carries one.  The learning domain exposes KC types
# (``LearningProgress.knowledge_types``), but the personalization signal path
# does not yet populate the field, so untyped signals decay with the default
# half-life until that plumbing lands.
HALF_LIFE_DAYS: dict[str, int] = {
    "MEMORY": 30,
    "PROCEDURE": 60,
    "CONCEPT": 90,
    "DESIGN": 120,
}
DEFAULT_HALF_LIFE_DAYS = 90

# Support-level thresholds kept in sync with the canonical projection in
# ``PersonalizationService`` (0.4 needs_support / 0.75 supported).
NEEDS_SUPPORT_THRESHOLD = 0.4
SUPPORTED_THRESHOLD = 0.75

SupportLevel = Literal["needs_support", "developing", "supported"]


def half_life_days(knowledge_type: str | None) -> float:
    """Half-life for a knowledge type; unknown/absent types use the default."""
    return float(HALF_LIFE_DAYS.get(str(knowledge_type or "").upper(), DEFAULT_HALF_LIFE_DAYS))


def decay_factor(days_since_last_practice: float, half_life: float) -> float:
    """Fraction of mastery-above-prior that survives after ``days`` (<= 1)."""
    if days_since_last_practice <= 0:
        return 1.0
    if half_life <= 0:
        raise ValueError("half_life must be positive")
    return math.exp(-math.log(2) * days_since_last_practice / half_life)


def effective_mastery(
    *,
    prior: float,
    posterior: float,
    days_since_last_practice: float,
    knowledge_type: str | None = None,
    half_life: float | None = None,
) -> float:
    """Exponential decay of the posterior back toward the prior."""
    hl = half_life if half_life is not None else half_life_days(knowledge_type)
    factor = decay_factor(days_since_last_practice, hl)
    return max(0.0, min(1.0, prior + (posterior - prior) * factor))


def days_since(iso_timestamp: str | None, *, now: datetime | None = None) -> float:
    """Days (fractional) between an ISO-8601 timestamp and now (never negative)."""
    if not iso_timestamp:
        return 0.0
    try:
        parsed = datetime.fromisoformat(str(iso_timestamp).replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    moment = now if now is not None else datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return max(0.0, (moment - parsed).total_seconds() / 86400.0)


def decayed_support_level(
    *,
    posterior: float,
    days_since_last_practice: float,
    prior: float = 0.2,
    knowledge_type: str | None = None,
    half_life: float | None = None,
) -> SupportLevel:
    """Reclassify a calibrated posterior after time decay."""
    effective = effective_mastery(
        prior=prior,
        posterior=posterior,
        days_since_last_practice=days_since_last_practice,
        knowledge_type=knowledge_type,
        half_life=half_life,
    )
    if effective < NEEDS_SUPPORT_THRESHOLD:
        return "needs_support"
    if effective >= SUPPORTED_THRESHOLD:
        return "supported"
    return "developing"


def project_concept_signal(
    signal: Any,
    *,
    now: datetime | None = None,
    knowledge_type: str | None = None,
    half_life: float | None = None,
    min_observations: int = 3,
) -> Any:
    """Return a decayed *copy* of one persisted concept signal.

    Only calibrated signals with enough verified observations are projected;
    everything else (recent failures, uncalibrated, too few observations,
    missing practice time) is returned unchanged.  The copy carries a
    ``decayed`` marker and re-derived ``mastery_probability`` /
    ``support_level`` so consumers (prompt context, component selector,
    display) can explain "why this page changed".
    """
    data = dict(signal.model_dump(mode="json"))
    if not bool(data.get("bkt_calibrated")):
        return signal
    if int(data.get("verified_observation_count") or 0) < min_observations:
        return signal
    if data.get("support_level") == "needs_support":
        # A recent verified failure is an explicit support need; time decay
        # must not quietly upgrade it.
        return signal
    posterior = data.get("mastery_probability")
    if not isinstance(posterior, (int, float)):
        return signal
    elapsed = days_since(data.get("last_practised_at"), now=now)
    if elapsed <= 0:
        return signal
    prior = float(data.get("initial_mastery_probability") or 0.2)
    # A typed signal carries its own knowledge type; the explicit argument
    # wins when both are present.
    if knowledge_type is None:
        knowledge_type = getattr(signal, "knowledge_type", None)
    projected = signal.model_copy(
        update={
            "mastery_probability": effective_mastery(
                prior=prior,
                posterior=float(posterior),
                days_since_last_practice=elapsed,
                knowledge_type=knowledge_type,
                half_life=half_life,
            ),
            "support_level": decayed_support_level(
                posterior=float(posterior),
                days_since_last_practice=elapsed,
                prior=prior,
                knowledge_type=knowledge_type,
                half_life=half_life,
            ),
        }
    )
    return projected


__all__ = [
    "DEFAULT_HALF_LIFE_DAYS",
    "HALF_LIFE_DAYS",
    "NEEDS_SUPPORT_THRESHOLD",
    "SUPPORTED_THRESHOLD",
    "decay_factor",
    "decayed_support_level",
    "days_since",
    "effective_mastery",
    "half_life_days",
    "project_concept_signal",
]
