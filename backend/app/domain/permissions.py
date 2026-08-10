"""Role permission model.

Permissions are conceptual capability flags. V1 enforces them through
SceneWorks orchestration (workspace selection, prompt instructions, whether
the Engineer's worktree is writable, commit policy) rather than through an
operating-system sandbox. The key invariant: a read-only role never receives
the same execution path as the Engineer.
"""

from __future__ import annotations

from enum import Enum


class Permission(str, Enum):
    REPOSITORY_READ = "repository_read"
    REPOSITORY_WRITE = "repository_write"
    SHELL_EXECUTE = "shell_execute"
    GIT_COMMIT = "git_commit"
    NETWORK_ACCESS = "network_access"
    TASK_STATE_CHANGE = "task_state_change"
    RESEARCH = "research"


def role_can(permissions: set[Permission], perm: Permission) -> bool:
    return perm in permissions
