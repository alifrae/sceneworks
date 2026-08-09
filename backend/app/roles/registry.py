"""Role registry and prompt loading.

Prompt files live in roles/prompts/*.md so they stay editable without code
changes. They are loaded once and cached; the PromptBuilder composes them
with project/task/worktree context at invocation time.
"""

from __future__ import annotations

from dataclasses import replace
from functools import lru_cache
from pathlib import Path

from app.roles.definitions import RoleDefinition, default_roles


class RoleNotFoundError(KeyError):
    pass


class RoleRegistry:
    def __init__(
        self,
        prompts_dir: Path,
        roles: list[RoleDefinition] | None = None,
        backend_overrides: dict[str, str] | None = None,
        default_backend: str | None = None,
    ):
        self._prompts_dir = Path(prompts_dir)
        self._roles = {r.key: r for r in (roles or default_roles())}
        self._overrides = backend_overrides or {}
        self._default_backend = default_backend

    def get(self, key: str) -> RoleDefinition:
        try:
            return self._roles[key]
        except KeyError:
            raise RoleNotFoundError(f"unknown role: {key}") from None

    def all(self) -> list[RoleDefinition]:
        return [self.effective(key) for key in self._roles]

    def effective(self, key: str) -> RoleDefinition:
        """Role as configured, with any backend override applied."""
        role = self.get(key)
        backend = self._overrides.get(key, self._default_backend or role.backend)
        if backend == role.backend:
            return role
        return replace(role, backend=backend)

    def system_instructions(self, role_key: str) -> str:
        """Standing instructions for a role. Falls back to a minimal built-in
        instruction set if the markdown file is missing."""
        role = self.get(role_key)
        if role.prompt_file:
            path = self._prompts_dir / role.prompt_file
        else:
            path = self._prompts_dir / f"{role_key}.md"
        if path.is_file():
            return path.read_text(encoding="utf-8")
        perms = ", ".join(sorted(p.value for p in role.permissions))
        return (
            f"You are the {role.display_name} of this software company.\n"
            f"Responsibilities: {', '.join(role.responsibilities)}\n"
            f"Your permissions are limited to: {perms or 'none'}.\n"
            "Never claim capabilities you do not have. Report clearly when a "
            "request falls outside your permissions."
        )

    @lru_cache(maxsize=1)
    def prompt_files(self) -> list[str]:
        if not self._prompts_dir.is_dir():
            return []
        return sorted(p.name for p in self._prompts_dir.glob("*.md"))
