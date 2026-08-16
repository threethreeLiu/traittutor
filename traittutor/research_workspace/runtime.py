"""Production composition and cross-owner dispatch for Research Workspace."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
import os
import socket
from uuid import uuid4

from traittutor.gateway.service import get_gateway
from traittutor.multi_user.context import get_current_user
from traittutor.multi_user.knowledge_access import resolve_for_rag
from traittutor.multi_user.models import CurrentUser, KnowledgeResource
from traittutor.multi_user.paths import user_context
from traittutor.operations import active_owner_contexts

from .executor import GatewayResearchExecutor, ResearchGateway, ResearchGatewayExecutionConfig
from .service import ResearchWorkspaceService
from .source_provider import (
    KnowledgeBaseValidatedSourceProvider,
    ResearchPolicySourceProvider,
    WebSearchValidatedSourceProvider,
)
from .store import ResearchWorkspaceStore
from .worker import ResearchWorkspaceWorker

ResearchExecutorFactory = Callable[[CurrentUser], GatewayResearchExecutor]


def owner_authorized_kb_resolver(user: CurrentUser) -> Callable[[str], KnowledgeResource | None]:
    """Re-enter one immutable owner context for every worker-time KB lookup."""

    def resolve(kb_ref: str) -> KnowledgeResource | None:
        with user_context(user):
            return resolve_for_rag(kb_ref)

    return resolve


def build_gateway_research_executor(
    user: CurrentUser,
    *,
    gateway: ResearchGateway | None = None,
) -> GatewayResearchExecutor:
    """Build the same owner-rechecked Gateway executor for HTTP and daemon workers."""

    return GatewayResearchExecutor(
        gateway or get_gateway(),
        source_provider=ResearchPolicySourceProvider(
            web=WebSearchValidatedSourceProvider(),
            knowledge_base=KnowledgeBaseValidatedSourceProvider(owner_authorized_kb_resolver(user)),
        ),
        config=ResearchGatewayExecutionConfig(),
        user_id=user.id,
    )


@dataclass(frozen=True, slots=True)
class ResearchDispatchResult:
    owner_id: str
    claimed: int
    failed: bool = False


def dispatch_research_once(
    *,
    owners: Iterable[CurrentUser] | None = None,
    executor_factory: ResearchExecutorFactory = build_gateway_research_executor,
    limit_per_owner: int = 10,
    worker_id: str | None = None,
) -> tuple[ResearchDispatchResult, ...]:
    """Recover queued/expired runs across owners with per-owner failure isolation."""

    if limit_per_owner < 1:
        raise ValueError("limit_per_owner must be positive")
    identity = worker_id or (
        f"research-daemon-{socket.gethostname()}-{os.getpid()}-{uuid4().hex[:8]}"
    )
    results: list[ResearchDispatchResult] = []
    for owner in owners or active_owner_contexts():
        try:
            with user_context(owner):
                if get_current_user().id != owner.id:
                    raise PermissionError("research dispatcher owner context mismatch")
                service = ResearchWorkspaceService(ResearchWorkspaceStore(owner.id))
                receipts = ResearchWorkspaceWorker(
                    service,
                    executor_factory(owner),
                ).recover_claimable(
                    worker_id=identity,
                    limit=limit_per_owner,
                )
            results.append(ResearchDispatchResult(owner_id=owner.id, claimed=len(receipts)))
        except Exception:
            # One corrupt owner store or unavailable provider must not starve
            # every other owner's queued work. Provider/user details are not
            # copied into this aggregate operational result.
            results.append(ResearchDispatchResult(owner_id=owner.id, claimed=0, failed=True))
    return tuple(results)


__all__ = [
    "ResearchDispatchResult",
    "build_gateway_research_executor",
    "dispatch_research_once",
    "owner_authorized_kb_resolver",
]
