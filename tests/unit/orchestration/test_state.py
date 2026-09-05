"""The state machine, tested exhaustively.

Every legal transition is asserted, and — more importantly — **every illegal one
is asserted to raise**. A state machine tested only on its happy paths is a
state machine that will silently accept the transition nobody thought of.
"""

from __future__ import annotations

import itertools

import pytest

from amos.orchestration.state import (
    TERMINAL_STATES,
    IllegalTransitionError,
    TaskState,
    assert_transition,
    can_transition,
    is_terminal,
)

LEGAL: set[tuple[TaskState, TaskState]] = {
    (TaskState.PENDING, TaskState.READY),
    (TaskState.PENDING, TaskState.SKIPPED),
    (TaskState.READY, TaskState.RUNNING),
    (TaskState.READY, TaskState.SKIPPED),
    (TaskState.RUNNING, TaskState.SUCCEEDED),
    (TaskState.RUNNING, TaskState.FAILED),
    (TaskState.RUNNING, TaskState.TIMED_OUT),
    (TaskState.FAILED, TaskState.READY),
    (TaskState.FAILED, TaskState.PERMANENTLY_FAILED),
    (TaskState.TIMED_OUT, TaskState.READY),
    (TaskState.TIMED_OUT, TaskState.PERMANENTLY_FAILED),
}

ALL_PAIRS = list(itertools.product(TaskState, TaskState))


@pytest.mark.parametrize(("current", "target"), sorted(LEGAL))
def test_legal_transitions_are_permitted(current: TaskState, target: TaskState) -> None:
    assert assert_transition(current, target) is target


@pytest.mark.parametrize(("current", "target"), [p for p in ALL_PAIRS if p not in LEGAL])
def test_every_other_transition_raises(current: TaskState, target: TaskState) -> None:
    """All 53 illegal combinations, not a hand-picked few."""
    with pytest.raises(IllegalTransitionError):
        assert_transition(current, target)


def test_the_legal_set_is_exactly_what_the_module_permits() -> None:
    """Guards against the table and this test drifting apart.

    If someone adds a transition to the module without adding it here, the two
    disagree and this fails — rather than the illegal-transition test quietly
    covering one case fewer.
    """
    actual = {(a, b) for a, b in ALL_PAIRS if can_transition(a, b)}
    assert actual == LEGAL


@pytest.mark.parametrize("state", sorted(TERMINAL_STATES))
def test_terminal_states_permit_nothing(state: TaskState) -> None:
    assert is_terminal(state)
    assert all(not can_transition(state, target) for target in TaskState)


def test_a_task_can_never_leave_succeeded() -> None:
    """The guarantee that matters most: nothing can un-succeed or re-run a
    completed task, including a confused caller or a model-driven code path."""
    for target in TaskState:
        with pytest.raises(IllegalTransitionError):
            assert_transition(TaskState.SUCCEEDED, target)


def test_retry_returns_to_ready_not_to_a_special_state() -> None:
    """A retried task re-enters the normal path, so it cannot behave differently
    from a first attempt."""
    assert can_transition(TaskState.FAILED, TaskState.READY)
    assert can_transition(TaskState.TIMED_OUT, TaskState.READY)
    assert not can_transition(TaskState.FAILED, TaskState.RUNNING)


def test_running_cannot_be_reentered() -> None:
    """Guards against double-execution: a RUNNING task cannot be started again."""
    assert not can_transition(TaskState.RUNNING, TaskState.RUNNING)
    assert not can_transition(TaskState.RUNNING, TaskState.READY)


def test_error_message_names_the_allowed_transitions() -> None:
    with pytest.raises(IllegalTransitionError) as exc:
        assert_transition(TaskState.SUCCEEDED, TaskState.RUNNING)
    assert "terminal" in exc.value.message
