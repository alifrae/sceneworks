"""Task state machine.

Centralized, explicit transitions. Tasks never change state via free-form
strings anywhere else in the codebase; all transitions flow through
TaskStateMachine.transition().
"""

from __future__ import annotations

from enum import Enum


class TaskStatus(str, Enum):
    NEW = "NEW"
    ARCHITECTURE_ANALYSIS = "ARCHITECTURE_ANALYSIS"
    AWAITING_ARCHITECTURE_APPROVAL = "AWAITING_ARCHITECTURE_APPROVAL"
    READY_TO_IMPLEMENT = "READY_TO_IMPLEMENT"
    IMPLEMENTING = "IMPLEMENTING"
    TESTING = "TESTING"
    REVIEWING = "REVIEWING"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    READY_FOR_HUMAN = "READY_FOR_HUMAN"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


TERMINAL = {TaskStatus.ACCEPTED, TaskStatus.REJECTED, TaskStatus.CANCELLED}
RUNNING = {TaskStatus.ARCHITECTURE_ANALYSIS, TaskStatus.IMPLEMENTING, TaskStatus.REVIEWING}


class TaskStateMachine:
    """Allowed transitions. Key: (from_status, action). Value: to_status."""

    ACTIONS = {
        "start_architecture",
        "architecture_completed",
        "approve_architecture",
        "reject_architecture",
        "request_architecture_revision",
        "start_implementation",
        "implementation_completed",
        "implementation_failed",
        "start_review",
        "review_completed",
        "review_changes_requested",
        "review_failed",
        "accept",
        "reject",
        "send_back_to_engineer",
        "cancel",
        "retry",
        "retry_architecture",
        "architecture_failed",
        "advisory_completed",
    }

    _TRANSITIONS: dict[tuple[TaskStatus, str], TaskStatus] = {
        (TaskStatus.NEW, "start_architecture"): TaskStatus.ARCHITECTURE_ANALYSIS,
        (TaskStatus.ARCHITECTURE_ANALYSIS, "architecture_completed"): (
            TaskStatus.AWAITING_ARCHITECTURE_APPROVAL
        ),
        (TaskStatus.ARCHITECTURE_ANALYSIS, "architecture_failed"): TaskStatus.FAILED,
        (TaskStatus.ARCHITECTURE_ANALYSIS, "cancel"): TaskStatus.CANCELLED,
        (TaskStatus.AWAITING_ARCHITECTURE_APPROVAL, "approve_architecture"): (
            TaskStatus.READY_TO_IMPLEMENT
        ),
        (TaskStatus.AWAITING_ARCHITECTURE_APPROVAL, "reject_architecture"): (
            TaskStatus.REJECTED
        ),
        (TaskStatus.AWAITING_ARCHITECTURE_APPROVAL, "request_architecture_revision"): (
            TaskStatus.ARCHITECTURE_ANALYSIS
        ),
        (TaskStatus.AWAITING_ARCHITECTURE_APPROVAL, "cancel"): TaskStatus.CANCELLED,
        (TaskStatus.READY_TO_IMPLEMENT, "start_implementation"): TaskStatus.IMPLEMENTING,
        (TaskStatus.READY_TO_IMPLEMENT, "cancel"): TaskStatus.CANCELLED,
        (TaskStatus.IMPLEMENTING, "implementation_completed"): TaskStatus.TESTING,
        (TaskStatus.IMPLEMENTING, "implementation_failed"): TaskStatus.FAILED,
        (TaskStatus.IMPLEMENTING, "cancel"): TaskStatus.CANCELLED,
        (TaskStatus.TESTING, "start_review"): TaskStatus.REVIEWING,
        (TaskStatus.TESTING, "cancel"): TaskStatus.CANCELLED,
        (TaskStatus.REVIEWING, "review_completed"): TaskStatus.READY_FOR_HUMAN,
        (TaskStatus.REVIEWING, "review_changes_requested"): TaskStatus.CHANGES_REQUESTED,
        (TaskStatus.REVIEWING, "review_failed"): TaskStatus.FAILED,
        (TaskStatus.REVIEWING, "cancel"): TaskStatus.CANCELLED,
        (TaskStatus.CHANGES_REQUESTED, "start_implementation"): TaskStatus.IMPLEMENTING,
        (TaskStatus.CHANGES_REQUESTED, "cancel"): TaskStatus.CANCELLED,
        (TaskStatus.READY_FOR_HUMAN, "accept"): TaskStatus.ACCEPTED,
        (TaskStatus.READY_FOR_HUMAN, "reject"): TaskStatus.REJECTED,
        (TaskStatus.READY_FOR_HUMAN, "send_back_to_engineer"): (
            TaskStatus.CHANGES_REQUESTED
        ),
        # Advisory-only (non-implementation) tasks complete here.
        (TaskStatus.ARCHITECTURE_ANALYSIS, "advisory_completed"): (
            TaskStatus.READY_FOR_HUMAN
        ),
        # Recoverable failure: retry re-enters the appropriate phase.
        # "retry" resumes an approved task at implementation; a task that never
        # produced an architecture must restart from the beginning instead,
        # because start_architecture is only valid from NEW.
        (TaskStatus.FAILED, "retry"): TaskStatus.READY_TO_IMPLEMENT,
        (TaskStatus.FAILED, "retry_architecture"): TaskStatus.NEW,
    }

    @classmethod
    def transition(cls, current: TaskStatus, action: str) -> TaskStatus:
        if action not in cls.ACTIONS:
            raise InvalidTransition(current, action, f"unknown action {action!r}")
        try:
            return cls._TRANSITIONS[(current, action)]
        except KeyError:
            raise InvalidTransition(
                current, action, f"no transition allowed from {current.value} via {action}"
            )

    @classmethod
    def can_transition(cls, current: TaskStatus, action: str) -> bool:
        return (current, action) in cls._TRANSITIONS

    @classmethod
    def allowed_actions(cls, current: TaskStatus) -> list[str]:
        return [action for (status, action) in cls._TRANSITIONS if status == current]


class InvalidTransition(Exception):
    def __init__(self, current: TaskStatus, action: str, message: str):
        self.current = current
        self.action = action
        super().__init__(message)
