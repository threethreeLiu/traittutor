"""Owner-bound composition shared by HTTP reads and operational workers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from traittutor.learning.storage import LearningStore
from traittutor.learning_model.events import LearnerEventLedger
from traittutor.learning_model.misconception import MisconceptionStore
from traittutor.multi_user.models import CurrentUser, UserScope
from traittutor.multi_user.paths import get_path_service_for_scope
from traittutor.services.path_service import PathService

from .repository import LearningGovernanceRepository, OwnerBoundLearningStore


@dataclass(frozen=True, slots=True)
class GovernanceStoreBundle:
    """The three existing truth sources scoped to one authenticated owner."""

    learning_store: LearningStore
    event_ledger: LearnerEventLedger
    misconception_store: MisconceptionStore


def default_governance_store_bundle(
    user: CurrentUser,
    *,
    path_service_resolver: Callable[[UserScope], PathService] = get_path_service_for_scope,
) -> GovernanceStoreBundle:
    path_service = path_service_resolver(user.scope)
    workspace = path_service.get_workspace_dir()
    learning_model_root = workspace / "learning_model"
    return GovernanceStoreBundle(
        learning_store=LearningStore(
            workspace / "learning",
            path_service=path_service,
            owner_id=user.id,
        ),
        event_ledger=LearnerEventLedger(
            learning_model_root / "learner_events.json",
            path_service=path_service,
        ),
        misconception_store=MisconceptionStore(
            learning_model_root / "misconceptions.json",
            owner_id=user.id,
            path_service=path_service,
        ),
    )


def build_governance_repository(
    user: CurrentUser,
    *,
    stores: GovernanceStoreBundle | None = None,
) -> LearningGovernanceRepository:
    resolved = stores or default_governance_store_bundle(user)
    return LearningGovernanceRepository(
        owner_id=user.id,
        learning_source=OwnerBoundLearningStore(
            owner_id=user.id,
            store=resolved.learning_store,
        ),
        event_ledger=resolved.event_ledger,
        misconception_store=resolved.misconception_store,
    )


__all__ = [
    "GovernanceStoreBundle",
    "build_governance_repository",
    "default_governance_store_bundle",
]
