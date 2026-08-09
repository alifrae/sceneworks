"""Unit tests: task state machine, permissions, roles, prompt builder."""

from __future__ import annotations

import pytest

from app.domain.permissions import Permission, role_can
from app.domain.task_states import (
    TERMINAL,
    InvalidTransition,
    TaskStateMachine,
    TaskStatus,
)


class TestStateMachine:
    def test_new_start_architecture(self):
        assert (
            TaskStateMachine.transition(TaskStatus.NEW, "start_architecture")
            == TaskStatus.ARCHITECTURE_ANALYSIS
        )

    def test_full_happy_path(self):
        steps = [
            ("start_architecture", TaskStatus.ARCHITECTURE_ANALYSIS),
            ("architecture_completed", TaskStatus.AWAITING_ARCHITECTURE_APPROVAL),
            ("approve_architecture", TaskStatus.READY_TO_IMPLEMENT),
            ("start_implementation", TaskStatus.IMPLEMENTING),
            ("implementation_completed", TaskStatus.TESTING),
            ("start_review", TaskStatus.REVIEWING),
            ("review_completed", TaskStatus.READY_FOR_HUMAN),
            ("accept", TaskStatus.ACCEPTED),
        ]
        current = TaskStatus.NEW
        for action, expected in steps:
            current = TaskStateMachine.transition(current, action)
            assert current == expected

    def test_changes_requested_loop(self):
        current = TaskStatus.NEW
        current = TaskStateMachine.transition(current, "start_architecture")
        current = TaskStateMachine.transition(current, "architecture_completed")
        current = TaskStateMachine.transition(current, "approve_architecture")
        current = TaskStateMachine.transition(current, "start_implementation")
        current = TaskStateMachine.transition(current, "implementation_completed")
        current = TaskStateMachine.transition(current, "start_review")
        current = TaskStateMachine.transition(current, "review_changes_requested")
        assert current == TaskStatus.CHANGES_REQUESTED
        current = TaskStateMachine.transition(current, "start_implementation")
        assert current == TaskStatus.IMPLEMENTING

    def test_reviewer_can_send_back_from_ready_for_human(self):
        current = TaskStatus.READY_FOR_HUMAN
        assert (
            TaskStateMachine.transition(current, "send_back_to_engineer")
            == TaskStatus.CHANGES_REQUESTED
        )

    def test_failed_retry(self):
        assert (
            TaskStateMachine.transition(TaskStatus.FAILED, "retry")
            == TaskStatus.READY_TO_IMPLEMENT
        )

    def test_invalid_transition_raises(self):
        with pytest.raises(InvalidTransition):
            TaskStateMachine.transition(TaskStatus.NEW, "accept")
        with pytest.raises(InvalidTransition):
            TaskStateMachine.transition(TaskStatus.NEW, "implementation_completed")

    def test_unknown_action_raises(self):
        with pytest.raises(InvalidTransition):
            TaskStateMachine.transition(TaskStatus.NEW, "no_such_action")

    def test_engineer_cannot_skip_approval(self):
        # start_implementation is only allowed from READY_TO_IMPLEMENT /
        # CHANGES_REQUESTED, never from NEW or AWAITING_ARCHITECTURE_APPROVAL.
        assert not TaskStateMachine.can_transition(TaskStatus.NEW, "start_implementation")
        assert not TaskStateMachine.can_transition(
            TaskStatus.AWAITING_ARCHITECTURE_APPROVAL, "start_implementation"
        )

    def test_terminal_states_have_no_actions(self):
        for status in TERMINAL:
            assert TaskStateMachine.allowed_actions(status) == []

    def test_architecture_revision_loop(self):
        current = TaskStateMachine.transition(TaskStatus.NEW, "start_architecture")
        current = TaskStateMachine.transition(current, "architecture_completed")
        current = TaskStateMachine.transition(
            current, "request_architecture_revision"
        )
        assert current == TaskStatus.ARCHITECTURE_ANALYSIS


class TestPermissions:
    def test_membership(self):
        assert role_can({Permission.REPOSITORY_READ}, Permission.REPOSITORY_READ)
        assert not role_can({Permission.REPOSITORY_READ}, Permission.GIT_COMMIT)

    def test_write_implies_read_permission_set(self):
        write = {Permission.REPOSITORY_READ, Permission.REPOSITORY_WRITE,
                 Permission.SHELL_EXECUTE, Permission.GIT_COMMIT}
        assert role_can(write, Permission.REPOSITORY_READ)
        assert role_can(write, Permission.GIT_COMMIT)


class TestRoleSeparation:
    def test_architect_is_read_only(self):
        from app.roles.definitions import default_roles

        roles = {r.key: r for r in default_roles()}
        assert not roles["architect"].can_modify_source
        assert not roles["architect"].can_commit
        assert Permission.REPOSITORY_WRITE not in roles["architect"].permissions
        assert Permission.GIT_COMMIT not in roles["architect"].permissions

    def test_engineer_can_modify(self):
        from app.roles.definitions import default_roles

        roles = {r.key: r for r in default_roles()}
        assert roles["engineer"].can_modify_source
        assert roles["engineer"].can_commit

    def test_reviewer_is_read_only(self):
        from app.roles.definitions import default_roles

        roles = {r.key: r for r in default_roles()}
        assert not roles["reviewer"].can_modify_source
        assert not roles["reviewer"].can_commit
