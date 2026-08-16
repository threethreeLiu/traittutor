"""WS-5B durable conversation and immutable-branch acceptance tests."""

from __future__ import annotations

import pytest

from traittutor.conversation import ConversationStore, RiskyInputRejected

NOW = "2026-08-09T08:00:00+00:00"


def test_cross_session_recovers_thread_episode_working_state_and_open_loop(tmp_path) -> None:
    path = tmp_path / "conversation.json"
    writer = ConversationStore("user-a", path=path)
    thread = writer.create_thread(title="Calculus", subject_id="math", created_at=NOW)
    first = writer.append_turn(thread.thread_id, role="user", content="Continue limits")
    second = writer.append_turn(
        thread.thread_id,
        role="assistant",
        content="We stopped at epsilon-delta.",
        source_refs=("source:notes",),
    )
    episode = writer.create_episode(
        thread.thread_id,
        start_turn_id=first.turn_id,
        end_turn_id=second.turn_id,
        task_type="tutoring",
        summary="Reviewed the definition of a limit.",
        source_refs=(first.turn_id, second.turn_id),
    )
    loop = writer.create_open_loop(
        thread.thread_id,
        title="Finish exercise 3",
        episode_id=episode.episode_id,
        source_turn_ids=(second.turn_id,),
    )
    state = writer.save_working_state(
        thread.thread_id,
        active_goal="Finish epsilon-delta practice",
        recent_turn_ids=(first.turn_id, second.turn_id),
        open_loop_ids=(loop.open_loop_id,),
    )

    reader = ConversationStore("user-a", path=path)
    assert reader.get_thread(thread.thread_id) is not None
    assert reader.get_thread_version(thread.thread_id) == f"{thread.thread_id}:v6"
    assert reader.get_episode(episode.episode_id).summary == episode.summary
    assert reader.get_working_state(thread.thread_id).state_id == state.state_id
    assert reader.list_open_loops(thread.thread_id)[0].open_loop_id == loop.open_loop_id


def test_edit_old_turn_creates_branch_without_mutating_published_turn(tmp_path) -> None:
    path = tmp_path / "conversation.json"
    store = ConversationStore("user-a", path=path)
    thread = store.create_thread(title="Branching", created_at=NOW)
    first = store.append_turn(thread.thread_id, role="user", content="Original question")
    answer = store.append_turn(thread.thread_id, role="assistant", content="Original answer")

    edited = store.append_turn(
        thread.thread_id,
        role="user",
        content="Corrected question",
        supersedes_turn_id=first.turn_id,
    )
    replacement = store.append_turn(thread.thread_id, role="assistant", content="New answer")

    assert store.get_turn(first.turn_id) == first
    assert store.get_turn(answer.turn_id) == answer
    assert edited.supersedes_turn_id == first.turn_id
    assert edited.branch_id != first.branch_id
    assert [turn.turn_id for turn in store.get_active_branch(thread.thread_id)] == [
        edited.turn_id,
        replacement.turn_id,
    ]
    disk_turns = store._adapter.snapshot()["turns"]
    assert next(turn for turn in disk_turns if turn["turn_id"] == first.turn_id)["content"] == (
        "Original question"
    )


def test_episode_and_open_loop_revisions_append_versions(tmp_path) -> None:
    store = ConversationStore("user-a", path=tmp_path / "conversation.json")
    thread = store.create_thread(title="Versions", created_at=NOW)
    turn = store.append_turn(thread.thread_id, role="user", content="Plan the work")
    episode = store.create_episode(
        thread.thread_id,
        start_turn_id=turn.turn_id,
        end_turn_id=turn.turn_id,
        task_type="planning",
        summary="Initial summary",
    )
    revised = store.revise_episode(episode.episode_id, summary="Corrected summary")
    loop = store.create_open_loop(thread.thread_id, title="Do the work")
    closed = store.transition_open_loop(loop.open_loop_id, status="completed")

    assert episode.summary == "Initial summary"
    assert revised.summary_version == 2
    assert revised.supersedes_episode_version == 1
    assert store.get_episode(episode.episode_id) == revised
    assert loop.status == "open"
    assert closed.status == "completed"
    assert closed.supersedes_version == 1
    assert store.list_open_loops(thread.thread_id) == []


def test_owner_isolation_applies_to_every_conversation_read(tmp_path) -> None:
    path = tmp_path / "conversation.json"
    owner = ConversationStore("user-a", path=path)
    thread = owner.create_thread(title="Private", created_at=NOW)
    turn = owner.append_turn(thread.thread_id, role="user", content="Secret")
    episode = owner.create_episode(
        thread.thread_id,
        start_turn_id=turn.turn_id,
        end_turn_id=turn.turn_id,
        task_type="chat",
        summary="Private episode",
    )
    loop = owner.create_open_loop(thread.thread_id, title="Private loop")
    owner.save_working_state(thread.thread_id, active_goal="Private goal")

    other = ConversationStore("user-b", path=path)
    assert other.get_thread(thread.thread_id) is None
    assert other.get_thread_version(thread.thread_id) is None
    assert other.get_turn(turn.turn_id) is None
    assert other.list_turns(thread.thread_id) == []
    assert other.get_episode(episode.episode_id) is None
    assert other.list_episodes(thread.thread_id) == []
    assert other.get_working_state(thread.thread_id) is None
    assert other.get_open_loop(loop.open_loop_id) is None
    assert other.list_open_loops(thread.thread_id) == []


def test_blocked_input_has_zero_persistent_side_effect(tmp_path) -> None:
    path = tmp_path / "conversation.json"
    store = ConversationStore("user-a", path=path)
    thread = store.create_thread(title="Safe", created_at=NOW)
    before = store._adapter.snapshot()

    with pytest.raises(RiskyInputRejected):
        store.append_turn(
            thread.thread_id,
            role="user",
            content="blocked",
            safety_status="blocked",
        )

    assert store._adapter.snapshot() == before
    assert store.list_turns(thread.thread_id) == []
