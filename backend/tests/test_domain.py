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


class TestTriageParsing:
    """Regression cover for a live failure.

    The Gemini ACP backend streams the agent's reply as deltas. Those deltas
    were re-joined with "\n", which inserted newlines at arbitrary chunk
    boundaries — inside string literals and even inside identifiers. The
    triage reply below is the shape actually observed against a live model:
    valid content, destroyed by reassembly, silently discarded by a parser
    that then defaulted `requires_implementation` to True for an explicitly
    read-only investigation.
    """

    def test_chunks_are_concatenated_not_newline_joined(self):
        from app.agents.gemini_acp import _join_text

        chunks = ['{"request_type": "technical_inv', 'estigation", "use_arch',
                  'itect": true, "requires_implementation": false}']
        assert _join_text(chunks) == (
            '{"request_type": "technical_investigation", '
            '"use_architect": true, "requires_implementation": false}'
        )

    def test_reassembled_stream_parses(self):
        from app.agents.gemini_acp import _join_text
        from app.roles.prompts import PromptBuilder

        chunks = ['{"request_type": "technical_inv', 'estigation", "use_technical_expert"',
                  ': true, "requires_implementation": false}']
        result = PromptBuilder.parse_triage_result(_join_text(chunks))
        assert result["request_type"] == "technical_investigation"
        assert result["use_technical_expert"] is True
        assert result["requires_implementation"] is False
        assert not result.get("triage_parse_failed")

    def test_json_in_markdown_fence_is_extracted(self):
        from app.roles.prompts import PromptBuilder

        text = (
            'Here is my classification:\n```json\n'
            '{"request_type": "bug", "use_cto": true, "requires_implementation": true}\n'
            '```\nLet me know if you need more.'
        )
        result = PromptBuilder.parse_triage_result(text)
        assert result["request_type"] == "bug"
        assert result["use_cto"] is True
        assert not result.get("triage_parse_failed")

    def test_nested_objects_and_braces_in_strings_are_handled(self):
        from app.roles.prompts import PromptBuilder

        text = (
            '{"request_type": "refactor", "meta": {"depth": {"n": 2}}, '
            '"reasoning_summary": "mentions {braces} and \\"quotes\\" inline", '
            '"requires_implementation": false}'
        )
        result = PromptBuilder.parse_triage_result(text)
        assert result["request_type"] == "refactor"
        assert result["requires_implementation"] is False
        assert not result.get("triage_parse_failed")

    def test_unparseable_output_is_flagged_not_silently_defaulted(self):
        from app.roles.prompts import PromptBuilder

        corrupted = '{\n  "request_type": "technical_investigation\n",\n  "use\n_architect": true\n}'
        result = PromptBuilder.parse_triage_result(corrupted)
        assert result["triage_parse_failed"] is True
        # Defaults still applied, but the caller can tell they are a guess.
        assert result["use_architect"] is True
        assert result["requires_implementation"] is True

    def test_empty_output_is_flagged(self):
        from app.roles.prompts import PromptBuilder

        assert PromptBuilder.parse_triage_result("")["triage_parse_failed"] is True
