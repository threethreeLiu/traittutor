"""Shared Bayesian Knowledge Tracing update math."""

from __future__ import annotations


def bkt_update(
    prior: float,
    *,
    correct: bool,
    transition: float,
    guess: float,
    slip: float,
    weight: float,
) -> float:
    """Return the weighted BKT posterior with the canonical live-path math."""
    predicted = prior + (1 - prior) * transition
    likelihood = (
        predicted * (1 - slip) + (1 - predicted) * guess
        if correct
        else predicted * slip + (1 - predicted) * (1 - guess)
    )
    posterior = (
        predicted if likelihood <= 0 else (predicted * (1 - slip if correct else slip)) / likelihood
    )
    return max(0.0, min(1.0, prior * (1 - weight) + posterior * weight))
