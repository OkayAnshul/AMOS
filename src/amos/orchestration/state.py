"""Task state machine.

The single most important property in AMOS's orchestration layer:

> **Only this module moves a task between states, and it raises on anything the
> diagram does not permit.**

No agent, no LLM output, no caller convenience path can put a task into a state
the transition table does not sanction. That is the concrete form of "LLMs handle
uncertainty; software handles guarantees" (docs/02-system-architecture.md). A
model that could set its own task to SUCCEEDED would make every guarantee in the
system advisory.

```
   PENDING ──deps satisfied──▶ READY ──claimed──▶ RUNNING
      │                          ▲                   │
      │                          │       ┌───────────┼───────────┐
      │                          │       ▼           ▼           ▼
      │                          │  SUCCEEDED     FAILED     TIMED_OUT
      │                          │                   │           │
      │                          └── retries left ───┴───────────┘
      │                                              │
      │                                       no retries left
      │                                              ▼
      └──dependency failed──▶ SKIPPED       PERMANENTLY_FAILED
```
"""

from __future__ import annotations

from enum import StrEnum

from amos.errors import AmosError


class TaskState(StrEnum):
    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    PERMANENTLY_FAILED = "PERMANENTLY_FAILED"
    SKIPPED = "SKIPPED"


#: Every legal transition. Anything absent here is a bug, not an edge case.
#: `FAILED`/`TIMED_OUT` are *transient* outcomes: they lead either back to READY
#: (a retry re-enters the normal path rather than taking a special one) or on to
#: PERMANENTLY_FAILED. Keeping them distinct from PERMANENTLY_FAILED is what makes
#: "was this retried?" answerable from the data.
_TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    TaskState.PENDING: frozenset({TaskState.READY, TaskState.SKIPPED}),
    TaskState.READY: frozenset({TaskState.RUNNING, TaskState.SKIPPED}),
    TaskState.RUNNING: frozenset({TaskState.SUCCEEDED, TaskState.FAILED, TaskState.TIMED_OUT}),
    TaskState.FAILED: frozenset({TaskState.READY, TaskState.PERMANENTLY_FAILED}),
    TaskState.TIMED_OUT: frozenset({TaskState.READY, TaskState.PERMANENTLY_FAILED}),
    # Terminal.
    TaskState.SUCCEEDED: frozenset(),
    TaskState.PERMANENTLY_FAILED: frozenset(),
    TaskState.SKIPPED: frozenset(),
}

TERMINAL_STATES = frozenset({TaskState.SUCCEEDED, TaskState.PERMANENTLY_FAILED, TaskState.SKIPPED})

#: A dependency in any of these will never succeed, so dependents are skipped.
UNRECOVERABLE_STATES = frozenset({TaskState.PERMANENTLY_FAILED, TaskState.SKIPPED})


class IllegalTransitionError(AmosError):
    """An attempt to move a task somewhere the state machine forbids.

    Deliberately an error rather than a warning. A silent illegal transition
    means the system's state no longer describes reality, and every later
    decision built on it is wrong — better to fail loudly at the point of the
    bug than to produce a plausible, incorrect trace.
    """

    def __init__(self, current: TaskState, target: TaskState) -> None:
        allowed = sorted(s.value for s in _TRANSITIONS[current])
        super().__init__(
            f"Cannot move a task from {current.value} to {target.value}. "
            f"Allowed from {current.value}: {', '.join(allowed) or 'nothing (terminal)'}",
            details={"current": current.value, "target": target.value, "allowed": allowed},
        )
        self.current = current
        self.target = target


def can_transition(current: TaskState, target: TaskState) -> bool:
    return target in _TRANSITIONS[current]


def assert_transition(current: TaskState, target: TaskState) -> TaskState:
    """Return `target` if the move is legal, otherwise raise.

    Every state change in the executor goes through here. It returns the target
    so call sites read as `task.state = assert_transition(task.state, NEXT)`,
    making it awkward to bypass.
    """
    if not can_transition(current, target):
        raise IllegalTransitionError(current, target)
    return target


def is_terminal(state: TaskState) -> bool:
    return state in TERMINAL_STATES
