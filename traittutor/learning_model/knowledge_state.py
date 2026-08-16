"""Per-KC knowledge-state units keyed by user+subject+kc (F-10, invariant #6).

The v2.7 state unit is keyed by ``user_id + subject_id + kc_id`` — never the
legacy book/kp dimension — so mastery is isolated per user, subject, and
concept (invariant #6). Each unit reuses the BKT-shaped fields of the existing
``ConceptSignal`` (mastery params + verified-observation count) but carries a
``param_version`` / ``calibrated`` flag so that uncalibrated parameters are
never shown as a precise posterior (WS-10 acceptance: 校准前只展示证据计数/区间).
"""

from __future__ import annotations

from math import sqrt
from threading import Lock
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .parameters import UNCALIBRATED_FALLBACK_PARAMS

# Below this many verified observations we do not trust a single posterior, so
# display surfaces show a count + wide interval instead of a pseudo-precise
# number (WS-10 / PRD §11: do not present an uncalibrated probability as fact).
MIN_OBSERVATIONS_FOR_PROBABILITY = 3


class KnowledgeStateKey(BaseModel):
    """Canonical isolation key: one mastery cell per user/subject/concept."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str = Field(min_length=1, max_length=96)
    subject_id: str = Field(min_length=1, max_length=96)
    kc_id: str = Field(min_length=1, max_length=160)


class KnowledgeStateUnit(BaseModel):
    """BKT-shaped mastery cell, versioned + calibration-flagged."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str = Field(min_length=1, max_length=96)
    subject_id: str = Field(min_length=1, max_length=96)
    kc_id: str = Field(min_length=1, max_length=160)
    mastery_probability: float = Field(default=UNCALIBRATED_FALLBACK_PARAMS.prior, ge=0, le=1)
    initial_mastery_probability: float = Field(
        default=UNCALIBRATED_FALLBACK_PARAMS.prior, ge=0, le=1
    )
    verified_observation_count: int = Field(default=0, ge=0)
    param_version: str = Field(
        default=UNCALIBRATED_FALLBACK_PARAMS.version, min_length=1, max_length=32
    )
    calibrated: bool = False
    updated_at: str

    def key(self) -> KnowledgeStateKey:
        return KnowledgeStateKey(user_id=self.user_id, subject_id=self.subject_id, kc_id=self.kc_id)


def display_mastery(unit: KnowledgeStateUnit) -> dict[str, Any]:
    """Build internal display facts; public DTOs exclude every numeric estimate."""
    from .stage_policy import EVIDENCE_STAGE_POLICY_VERSION, qualitative_evidence_state

    probability: float | None
    if unit.calibrated and unit.verified_observation_count >= MIN_OBSERVATIONS_FOR_PROBABILITY:
        observations = unit.verified_observation_count
        probability = unit.mastery_probability
        z = 1.96
        z_squared = z**2
        denominator = 1 + z_squared / observations
        centre = (probability + z_squared / (2 * observations)) / denominator
        margin = (
            z
            * sqrt(
                probability * (1 - probability) / observations + z_squared / (4 * observations**2)
            )
            / denominator
        )
        interval = (max(0.0, centre - margin), min(1.0, centre + margin))
    else:
        interval = (0.0, 1.0)
        probability = None
    return {
        "verified_observation_count": unit.verified_observation_count,
        "evidence_state": qualitative_evidence_state(unit),
        "change_signal": "none",
        "model_version": unit.param_version,
        "stage_policy_version": EVIDENCE_STAGE_POLICY_VERSION,
        "mastery_probability": probability,
        "mastery_interval": interval,
        "calibrated": unit.calibrated,
    }


class KnowledgeStateStore:
    """Process-local store keyed by user+subject+kc."""

    def __init__(self) -> None:
        self._units: dict[KnowledgeStateKey, KnowledgeStateUnit] = {}
        self._lock = Lock()

    def get(self, key: KnowledgeStateKey) -> KnowledgeStateUnit | None:
        with self._lock:
            return self._units.get(key)

    def get_or_seed(self, key: KnowledgeStateKey, *, now: str) -> KnowledgeStateUnit:
        # Seed lookup and insertion must be indivisible so every caller receives
        # the same canonical frozen state object for this isolation key.
        with self._lock:
            seeded = self._units.get(key)
            if seeded is not None:
                return seeded
            unit = KnowledgeStateUnit(
                user_id=key.user_id,
                subject_id=key.subject_id,
                kc_id=key.kc_id,
                updated_at=now,
            )
            self._units[key] = unit
            return unit

    def upsert(self, unit: KnowledgeStateUnit) -> None:
        with self._lock:
            self._units[unit.key()] = unit

    def all_for(self, *, user_id: str, subject_id: str) -> list[KnowledgeStateUnit]:
        with self._lock:
            return [
                unit
                for unit in self._units.values()
                if unit.user_id == user_id and unit.subject_id == subject_id
            ]
