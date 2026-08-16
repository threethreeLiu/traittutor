"""Versioned BKT parameters + canonical strong-evidence update (F-10, invariant #2).

The update math is reused from the shared canonical Bayesian function
(``traittutor.personalization.bkt_math.bkt_update``) so this module converges
with — rather than diverges from — the live BKT. What this module adds is the
v2.7 wrapping the WS-10 plan asks for: a *versioned* parameter set, a
single ``is_strong_evidence`` gate so exposure events can never reach BKT, and
a full-update weight policy (weight=1.0) for the canonical path. Live/rebuild
reads resolve the same deployment-owned calibration artifact; the retired
recency heuristic remains available only behind an explicit rollback switch.
"""

from __future__ import annotations

from traittutor.personalization.bkt_math import bkt_update

from .events import LearnerEvent, is_strong_evidence
from .knowledge_state import KnowledgeStateKey, KnowledgeStateStore, KnowledgeStateUnit
from .parameters import BKTParamSet, get_active_bkt_params


def update_with_evidence(
    state: KnowledgeStateUnit,
    event: LearnerEvent,
    params: BKTParamSet | None = None,
    *,
    now: str,
) -> KnowledgeStateUnit:
    """Apply one strong-evidence event to a state unit, else return it unchanged.

    Returns the unit UNCHANGED when the event is not strong evidence (exposure,
    self-report, unreliable attribution, ungraded). This is the only entry
    point that moves ``mastery_probability`` (invariant #2). ``weight=1.0`` is
    the canonical full-update policy for the v2.7 path.
    """
    if not is_strong_evidence(event):
        return state
    active_params = params or get_active_bkt_params()
    prior = state.mastery_probability
    posterior = bkt_update(
        prior,
        correct=bool(event.answer_correct),
        transition=active_params.transition,
        guess=active_params.guess,
        slip=active_params.slip,
        weight=1.0,
    )
    return KnowledgeStateUnit(
        user_id=state.user_id,
        subject_id=state.subject_id,
        kc_id=state.kc_id,
        mastery_probability=posterior,
        initial_mastery_probability=state.initial_mastery_probability,
        verified_observation_count=state.verified_observation_count + 1,
        param_version=active_params.version,
        calibrated=active_params.calibrated,
        updated_at=now,
    )


def rebuild_knowledge_states(
    events: list[LearnerEvent],
    params: BKTParamSet | None = None,
) -> KnowledgeStateStore:
    """Deterministically rebuild every user+subject+KC state from a stream.

    Events without a subject or reliable strong evidence remain in the ledger
    but cannot create a BKT cell.  Sorting includes ``event_id`` so the same
    event stream and parameter version produces the same result even when the
    persistent JSON order was rewritten.
    """
    active_params = params or get_active_bkt_params()
    store = KnowledgeStateStore()
    for event in sorted(events, key=lambda item: (item.created_at, item.event_id)):
        if not is_strong_evidence(event) or event.subject_id is None:
            continue
        for kc_id in event.kc_ids:
            key = KnowledgeStateKey(
                user_id=event.user_id,
                subject_id=event.subject_id,
                kc_id=kc_id,
            )
            state = store.get_or_seed(key, now=event.created_at)
            if state.verified_observation_count == 0:
                state = state.model_copy(
                    update={
                        "mastery_probability": active_params.prior,
                        "initial_mastery_probability": active_params.prior,
                        "param_version": active_params.version,
                        "calibrated": active_params.calibrated,
                    }
                )
            store.upsert(
                update_with_evidence(
                    state,
                    event,
                    params=active_params,
                    now=event.created_at,
                )
            )
    return store
