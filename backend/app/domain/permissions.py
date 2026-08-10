"""Role permission model.

Permissions are capability flags carried on a role and copied into each
execution's workspace. They are enforced by SceneWorks orchestration, not by
an operating-system sandbox. The key invariant: a read-only role never
receives the same execution path as the Engineer.

Which flags actually gate behaviour, as of V3.0:

- REPOSITORY_WRITE  -> AgentPolicy.allow_write. Without it the ACP proxy
  refuses every fs/write_text_file.
- SHELL_EXECUTE     -> AgentPolicy.allow_shell. Without it the ACP proxy
  refuses terminal/create, and 'execute' tool permissions are denied.

The remaining members are declarative only — they document intent and appear
in prompts and the UI, but nothing checks them at runtime:

- REPOSITORY_READ   implied by having a worktree at all; reads are bounded by
  the workspace confinement check, not by this flag.
- GIT_COMMIT        committing is a shell command, so it is governed by
  SHELL_EXECUTE.
- NETWORK_ACCESS    the agent runtime's own network access is not mediated.
- RESEARCH          descriptive.
- TASK_STATE_CHANGE unused; no role is granted it and nothing reads it.

Note that SHELL_EXECUTE is the wide one: a shell command can reach anything
the OS lets the SceneWorks user reach. File access is confined to the pinned
worktree; shell is not sandboxed. See docs/limitations.md.
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
