from __future__ import annotations

import pytest

from traittutor.research_workspace.state_machine import (
    ResearchRunTransitionError,
    can_transition,
    require_transition,
)


def test_research_run_transition_table_is_closed() -> None:
    assert can_transition("draft", "queued")
    assert can_transition("running", "completed")
    assert can_transition("paused", "queued")
    assert not can_transition("completed", "running")
    assert not can_transition("cancelled", "queued")


def test_terminal_run_cannot_be_revived_by_late_worker_result() -> None:
    with pytest.raises(ResearchRunTransitionError):
        require_transition("cancelled", "completed")

    with pytest.raises(ResearchRunTransitionError):
        require_transition("completed", "running")
