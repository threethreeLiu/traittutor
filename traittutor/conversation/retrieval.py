"""Owner-bound, bounded retrieval of safe conversation episode summaries.

The durable conversation store contains immutable L0 transcript turns as well
as derived L2 episodes.  Prompt assembly must never copy an arbitrary branch
or a full transcript.  This service is the one narrow read boundary that
selects a current branch's closed episodes and exposes a small, screened
summary only after owner and partition checks have succeeded.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from traittutor.learning.intent import scan_untrusted_learning_payload

from .models import ConversationThread
from .store import ConversationStore

_MAX_EPISODES = 2
_MAX_SUMMARY_CHARS = 480


@dataclass(frozen=True)
class ConversationEpisodeSlice:
    """A prompt-safe L2 episode slice, never a transcript or answer record."""

    episode_id: str
    summary_version: int
    task_type: str
    summary: str


@dataclass(frozen=True)
class ConversationRetrievalResult:
    """Owner-authorized current-thread retrieval result.

    ``episodes`` contains at most two screened summaries.  An absent result is
    an ordinary no-memory state; ``degradation_reasons`` is reserved for a
    selected current episode that failed the safety boundary.
    """

    thread_id: str | None = None
    thread_version: str | None = None
    active_branch_version: str | None = None
    episodes: tuple[ConversationEpisodeSlice, ...] = ()
    degradation_reasons: tuple[str, ...] = ()


class ConversationRetrievalService:
    """Read safe episode context without widening conversation authorization.

    The store is owner-bound at construction.  Subject and project are exact
    partition constraints, including ``None``: an unscoped prompt must not
    accidentally recall a subject- or project-scoped conversation.  Only the
    current active branch may contribute a closed, non-sensitive episode.
    """

    def __init__(
        self,
        owner_id: str,
        *,
        store_factory: Callable[[str], ConversationStore] | None = None,
    ) -> None:
        if not owner_id.strip():
            raise ValueError("owner_id is required")
        self.owner_id = owner_id
        self._store_factory = store_factory

    def retrieve(
        self,
        thread_or_session_id: str,
        *,
        subject_id: str | None,
        project_id: str | None,
        limit: int = _MAX_EPISODES,
    ) -> ConversationRetrievalResult:
        """Return bounded context for one exact owner/subject/project scope.

        Session ids resolve through the durable owner-bound session mapping,
        which makes reconnects recover the same canonical thread.  Foreign,
        stale, deleted, archived, wrong-subject, and wrong-project objects all
        look like unavailable context rather than exposing their existence.
        """
        requested_id = thread_or_session_id.strip()
        if not requested_id or limit <= 0:
            return ConversationRetrievalResult()

        # Resolve the default lazily so deployment/test storage seams can
        # replace ``conversation.store.ConversationStore`` without this module
        # pinning an older imported class at process startup.
        if self._store_factory is None:
            from .store import ConversationStore as Store

            store = Store(self.owner_id)
        else:
            store = self._store_factory(self.owner_id)
        if store.owner_id != self.owner_id:
            raise PermissionError("conversation store does not own the context user")
        thread = store.get_thread(requested_id) or store.get_thread_for_session(requested_id)
        if not self._is_authorized_thread(
            thread,
            subject_id=subject_id,
            project_id=project_id,
        ):
            return ConversationRetrievalResult()
        assert thread is not None  # narrowed by _is_authorized_thread

        active_turn_ids = {turn.turn_id for turn in store.get_active_branch(thread.thread_id)}
        if not active_turn_ids:
            return ConversationRetrievalResult(
                thread_id=thread.thread_id,
                thread_version=f"{thread.thread_id}:v{thread.version}",
                active_branch_version=store.get_active_branch_version(thread.thread_id),
            )

        slices: list[ConversationEpisodeSlice] = []
        reasons: list[str] = []
        for episode in reversed(store.list_episodes(thread.thread_id)):
            if len(slices) >= min(limit, _MAX_EPISODES):
                break
            # A revised/deleted/superseded episode is resolved by
            # ``list_episodes``/``get_episode`` to its latest version.  Only
            # a currently closed, non-sensitive derivative over the active
            # branch can be offered to a prompt.
            if (
                episode.status != "closed"
                or episode.sensitivity == "sensitive"
                or episode.branch_id != thread.active_branch_id
                or episode.start_turn_id not in active_turn_ids
                or episode.end_turn_id not in active_turn_ids
            ):
                continue
            candidate = " ".join(episode.summary.split())[:_MAX_SUMMARY_CHARS]
            action, _category = scan_untrusted_learning_payload(
                {"task_type": episode.task_type, "summary": candidate}
            )
            if action == "block":
                reasons.append("conversation_episode_content_rejected")
                continue
            if not candidate:
                # Empty summary has no prompt value, but is not an error and
                # must not create a second derived state just to fill it.
                continue
            slices.append(
                ConversationEpisodeSlice(
                    episode_id=episode.episode_id,
                    summary_version=episode.summary_version,
                    task_type=episode.task_type,
                    summary=candidate,
                )
            )

        return ConversationRetrievalResult(
            thread_id=thread.thread_id,
            thread_version=f"{thread.thread_id}:v{thread.version}",
            active_branch_version=store.get_active_branch_version(thread.thread_id),
            episodes=tuple(reversed(slices)),
            degradation_reasons=tuple(dict.fromkeys(reasons)),
        )

    @staticmethod
    def _is_authorized_thread(
        thread: ConversationThread | None,
        *,
        subject_id: str | None,
        project_id: str | None,
    ) -> bool:
        return bool(
            thread is not None
            and thread.status == "active"
            and thread.subject_id == subject_id
            and thread.project_id == project_id
        )
