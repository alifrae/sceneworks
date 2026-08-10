"""Prompt building layer.

Composes role instructions + project context + task context + workspace info
+ permissions + requested output format into one system prompt and one user
prompt. No API route handler ever builds prompts directly.
"""

from __future__ import annotations

import json
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

TRIAGE_SYSTEM_PROMPT = (
    "You are a request triage classifier. Your job is to analyze a task "
    "description and determine what kind of work it represents and which "
    "company roles should participate.\n\n"
    "Classify the request into one of these types:\n"
    "- bug\n"
    "- feature\n"
    "- architecture\n"
    "- technical_investigation\n"
    "- refactor\n"
    "- product_question\n"
    "- technology_decision\n\n"
    "For each advisory role, decide whether it materially contributes:\n"
    "- Product: when user-facing behavior, requirements, scope, or "
    "acceptance criteria need definition.\n"
    "- CTO: when technology choices, build-vs-buy, migrations, platform "
    "decisions, or major feasibility/risk questions are involved.\n"
    "- Technical Expert: when specialized domain/algorithmic correctness, "
    "constraints, or performance implications need evaluation.\n"
    "- Architect: almost always, unless the request is purely a product "
    "question or simple question that does not involve the codebase.\n\n"
    "Also determine whether code implementation is needed.\n\n"
    "Return ONLY a valid JSON object with this schema:\n"
    '{"request_type": "...", "use_product": bool, "use_cto": bool, '
    '"use_architect": bool, "use_technical_expert": bool, '
    '"requires_implementation": bool, "reasoning_summary": "..."}\n\n'
    "Do not include any other text outside the JSON."
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
        is_correction: bool = False,
        upstream_contexts: dict[str, str] | None = None,
        context_worktree_path: str | None = None,
    ) -> BuiltPrompt:
        extra = extra or {}
        upstream_contexts = upstream_contexts or {}
        context_files = await self._read_project_context(project, context_worktree_path)
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

        # Upstream advisory context for Architect and Engineer.
        upstream_block = ""
        if upstream_contexts and role.key in ("architect", "engineer"):
            for label, content in upstream_contexts.items():
                if content:
                    upstream_block += (
                        f"\n\n## {label}\n" + _cap(content, 30_000)
                    )
        if upstream_block:
            upstream_block = "\n\n# Upstream analysis\n" + upstream_block

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

        # Correction iteration prefix for engineer.
        correction_prefix = ""
        if is_correction and role.key == "engineer":
            correction_prefix = (
                "This is a correction iteration.\n\n"
                "Do not reimplement the task from scratch.\n"
                "Address the Reviewer findings only, plus any directly "
                "required supporting changes.\n\n"
                "Continue from the current implementation state in the "
                "worktree — do not discard previous commits.\n\n"
            )

        user = (
            f"{correction_prefix}"
            f"Execute your responsibilities for this request. Be precise and "
            f"concise; do not invent facts."
            f"{project_block}{task_block}{upstream_block}{context_block}"
            f"{workspace_block}{extra_block}"
        )
        if role.key == "reviewer":
            user += REVIEW_VERDICT_LINE

        system = self._roles.system_instructions(role.key)
        return BuiltPrompt(system=system, user=user, context_files=[])

    @staticmethod
    def build_triage_prompt(task: Task, project: Project) -> tuple[str, str]:
        """Build a system/user prompt pair for the triage node."""
        system = TRIAGE_SYSTEM_PROMPT
        user = (
            "Classify the following task:\n\n"
            f"Project: {project.name}\n"
            f"Task: {task.title}\n"
            f"Description: {task.description or '(none)'}"
        )
        return system, user

    @staticmethod
    def _extract_json_object(text: str) -> dict | None:
        """Pull the first complete JSON object out of model output.

        Models wrap JSON in prose or in a ```json fence, and the object can
        contain nested objects. A non-greedy `\\{[^{}]*\\}` regex matched
        neither case, so a perfectly good reply was discarded. Scan for a
        balanced object instead, ignoring braces inside strings.
        """
        for start, char in enumerate(text):
            if char != "{":
                continue
            depth = 0
            in_string = False
            escaped = False
            for index in range(start, len(text)):
                current = text[index]
                if in_string:
                    if escaped:
                        escaped = False
                    elif current == "\\":
                        escaped = True
                    elif current == '"':
                        in_string = False
                    continue
                if current == '"':
                    in_string = True
                elif current == "{":
                    depth += 1
                elif current == "}":
                    depth -= 1
                    if depth == 0:
                        candidate = text[start : index + 1]
                        try:
                            parsed = json.loads(candidate)
                        except (json.JSONDecodeError, TypeError):
                            break
                        if isinstance(parsed, dict):
                            return parsed
                        break
        return None

    @staticmethod
    def parse_triage_result(text: str) -> dict:
        """Extract a triage decision from model output.

        Returns defaults on parse failure, with `triage_parse_failed` set so
        the caller can report that routing was not actually decided by triage
        rather than presenting the fallback as a real decision.
        """
        parsed = PromptBuilder._extract_json_object(text or "")
        if parsed is not None:
            return {
                "request_type": str(parsed.get("request_type", "feature")).strip(),
                "use_product": bool(parsed.get("use_product", False)),
                "use_cto": bool(parsed.get("use_cto", False)),
                "use_architect": bool(parsed.get("use_architect", True)),
                "use_technical_expert": bool(parsed.get("use_technical_expert", False)),
                "requires_implementation": bool(parsed.get("requires_implementation", True)),
                "reasoning_summary": str(parsed.get("reasoning_summary", "")),
            }
        return {
            "request_type": "feature",
            "use_product": False,
            "use_cto": False,
            "use_architect": True,
            "use_technical_expert": False,
            "requires_implementation": True,
            "reasoning_summary": "triage output could not be parsed; defaulting to "
            "architect path — participant selection and requires_implementation "
            "were NOT determined by triage",
            "triage_parse_failed": True,
        }

    # ------------------------------------------------------------------ files

    async def _read_project_context(
        self, project: Project | None, worktree_path: str | None = None,
    ) -> str:
        """Read configured + default context files from the repository or a
        specified worktree.

        When worktree_path is provided, context is read from that snapshot
        (committed state). Otherwise falls back to project.repository_path
        (human working tree — only for conversational company asks without a
        task).
        """
        if project is None:
            return ""
        base = Path(worktree_path).resolve() if worktree_path else Path(project.repository_path).resolve()
        if not base.is_dir():
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
            path = (base / rel).resolve()
            try:
                if not path.is_relative_to(base):
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
