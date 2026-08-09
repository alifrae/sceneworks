"""Prompt building layer.

Composes role instructions + project context + task context + workspace info
+ permissions + requested output format into one system prompt and one user
prompt. No API route handler ever builds prompts directly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from app.config.settings import Settings
from app.models import Project, Task
from app.roles.definitions import RoleDefinition
from app.roles.registry import RoleRegistry

logger = logging.getLogger("sceneworks.prompts")

DEFAULT_CONTEXT_FILES = ("AGENTS.md", "ARCHITECTURE.md", "CONTRIBUTING.md", "ROADMAP.md")

REVIEW_VERDICT_LINE = (
    "\nEnd your response with exactly one line: `VERDICT: APPROVED` or "
    "`VERDICT: CHANGES_REQUESTED`."
)


@dataclass
class BuiltPrompt:
    system: str
    user: str
    context_files: list[str] = field(default_factory=list)


class PromptBuilder:
    def __init__(self, settings: Settings, roles: RoleRegistry):
        self._settings = settings
        self._roles = roles

    async def build(
        self,
        *,
        role: RoleDefinition,
        project: Project | None,
        task: Task | None,
        workspace: dict,
        extra: dict | None = None,
    ) -> BuiltPrompt:
        extra = extra or {}
        context_files = await self._read_project_context(project)
        context_block = ""
        if context_files:
            context_block = (
                "\n\n# Project context files (authoritative reference)\n"
                + context_files
            )

        task_block = ""
        if task is not None:
            task_block = (
                "\n\n# Task\n"
                f"- Title: {task.title}\n"
                f"- Status: {task.status}\n"
                f"- Priority: {task.priority or 'medium'}\n"
                f"- Description:\n{task.description or '(none)'}"
            )
            if task.architecture_result and role.key in ("engineer", "reviewer"):
                task_block += (
                    "\n- Approved architecture analysis:\n"
                    + _cap(task.architecture_result, 20_000)
                )
            if task.implementation_summary and role.key == "reviewer":
                task_block += (
                    "\n- Engineer's implementation summary:\n"
                    + _cap(task.implementation_summary, 10_000)
                )
            if task.review_result and role.key == "engineer":
                task_block += (
                    "\n- Reviewer feedback (address all items):\n"
                    + _cap(task.review_result, 10_000)
                )

        workspace_block = (
            "\n\n# Workspace\n"
            f"- Working directory (cwd): {workspace.get('cwd', '')}\n"
            f"- Repository: {workspace.get('repo_path', '')}\n"
            f"- Branch: {workspace.get('branch') or 'detached'}\n"
            f"- Base commit: {workspace.get('base_commit') or 'unknown'}\n"
            f"- Your permissions: {', '.join(workspace.get('permissions') or []) or 'none'}"
        )

        project_block = ""
        if project is not None:
            project_block = (
                "\n\n# Project\n"
                f"- Name: {project.name}\n"
                f"- Description: {project.description or '(none)'}\n"
                f"- Default branch: {project.default_branch or 'unknown'}"
            )
            if project.test_commands:
                project_block += "\n- Configured test commands:\n" + "\n".join(
                    f"  - `{c}`" for c in project.test_commands
                )
            if project.build_commands:
                project_block += "\n- Configured build commands:\n" + "\n".join(
                    f"  - `{c}`" for c in project.build_commands
                )

        extra_block = ""
        for key, value in extra.items():
            if value:
                extra_block += f"\n\n# {key.replace('_', ' ').title()}\n{value}"

        user = (
            f"Execute your responsibilities for this request. Be precise and "
            f"concise; do not invent facts."
            f"{project_block}{task_block}{context_block}{workspace_block}{extra_block}"
        )
        if role.key == "reviewer":
            user += REVIEW_VERDICT_LINE

        system = self._roles.system_instructions(role.key)
        return BuiltPrompt(system=system, user=user, context_files=[])

    # ------------------------------------------------------------------ files

    async def _read_project_context(self, project: Project | None) -> str:
        """Read configured + default context files from the repository.

        Plain file reads only — no RAG, no embeddings, no vector store.
        Paths are validated to stay inside the repository.
        """
        if project is None:
            return ""
        repo = Path(project.repository_path).resolve()
        if not repo.is_dir():
            return ""
        candidates: list[str] = []
        for p in project.architecture_context_paths or []:
            if isinstance(p, str) and p.strip():
                candidates.append(p.strip())
        for p in DEFAULT_CONTEXT_FILES:
            candidates.append(p)
        seen: set[str] = set()
        parts: list[str] = []
        total = 0
        limit = self._settings.context_max_bytes
        for rel in candidates:
            if rel in seen:
                continue
            seen.add(rel)
            path = (repo / rel).resolve()
            try:
                if not path.is_relative_to(repo):
                    logger.warning("context path escapes repository: %s", rel)
                    continue
            except ValueError:
                continue
            if not path.is_file():
                continue
            try:
                data = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                logger.warning("could not read context file %s: %s", rel, exc)
                continue
            data = _cap(data, self._settings.context_file_max_bytes)
            total += len(data.encode("utf-8"))
            if total > limit:
                logger.warning("project context exceeds %s bytes; truncating", limit)
                break
            parts.append(f"--- {rel} ---\n{data}")
        return "\n\n".join(parts)


def _cap(text: str, max_bytes: int) -> str:
    if len(text.encode("utf-8")) <= max_bytes:
        return text
    return text.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore") + "\n[truncated]"
