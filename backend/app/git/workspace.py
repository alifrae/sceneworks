"""Isolated Git workspace service.

All agent file access happens inside worktrees created by this service.
The human working tree is never touched by agents.

Safety rules enforced here:
- the repository must exist and be a Git repository before anything runs;
- worktree destinations must live under the configured worktree root;
- existing worktrees/branches are never silently overwritten;
- git never prompts interactively (GIT_TERMINAL_PROMPT=0, timeout).
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path

from app.config.settings import Settings

GIT_TIMEOUT_SECONDS = 120

_AGENT_ENV = {"GIT_TERMINAL_PROMPT": "0", "GIT_PAGER": "cat"}


class GitError(Exception):
    """Raised when a git command or workspace operation fails."""


@dataclass
class RepoInfo:
    path: Path
    is_git: bool
    head_branch: str | None
    head_commit: str | None
    error: str | None = None


@dataclass
class WorkspaceInfo:
    worktree_path: Path
    base_commit: str | None
    branch: str | None
    detached: bool


async def run_git(cwd: Path, *args: str, timeout: int = GIT_TIMEOUT_SECONDS) -> str:
    """Run a git command asynchronously; return stdout. Raise GitError on failure."""
    env = {**os.environ, **_AGENT_ENV}
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise GitError(f"git command timed out after {timeout}s: git {' '.join(args)}")
    if proc.returncode != 0:
        detail = stderr.decode(errors="replace").strip() or stdout.decode(errors="replace").strip()
        raise GitError(f"git {' '.join(args)} failed ({proc.returncode}): {detail}")
    return stdout.decode(errors="replace")


class GitWorktreeService:
    def __init__(self, settings: Settings):
        self._settings = settings

    @property
    def worktree_root(self) -> Path:
        root = self._settings.worktree_root
        return root if root.is_absolute() else (Path.cwd() / root).resolve()

    # ------------------------------------------------------------------ info

    async def repo_info(self, repo_path: Path) -> RepoInfo:
        repo_path = repo_path.resolve()
        if not repo_path.is_dir():
            return RepoInfo(repo_path, False, None, None, "path is not a directory")
        try:
            head = await run_git(repo_path, "rev-parse", "--abbrev-ref", "HEAD")
            commit = await run_git(repo_path, "rev-parse", "HEAD")
        except GitError as exc:
            return RepoInfo(repo_path, False, None, None, str(exc))
        branch = head.strip() if head.strip() != "HEAD" else None
        return RepoInfo(repo_path, True, branch, commit.strip() or None)

    async def resolve_base_commit(self, repo_path: Path, branch: str | None) -> str:
        """Resolve the commit a task should be based on (default branch head)."""
        if branch:
            try:
                out = await run_git(repo_path, "rev-parse", "--verify", f"{branch}^{{commit}}")
                return out.strip()
            except GitError:
                pass
        out = await run_git(repo_path, "rev-parse", "HEAD")
        return out.strip()

    async def ensure_branch_available(self, repo_path: Path, branch: str) -> bool:
        """True if the branch does not exist yet (safe to create)."""
        out = await run_git(repo_path, "branch", "--list", branch)
        return out.strip() == ""

    # ------------------------------------------------------- worktree creation

    def _dest(self, repo_name: str, task_id: int, suffix: str) -> Path:
        dest = self.worktree_root / f"{repo_name}-sw-task-{task_id}{suffix}"
        if not dest.resolve().is_relative_to(self.worktree_root.resolve()):
            raise GitError("invalid worktree destination (outside worktree root)")
        return dest

    async def create_detached_worktree(
        self, repo_path: Path, base_commit: str, task_id: int, suffix: str = "-arch"
    ) -> WorkspaceInfo:
        dest = self._dest(repo_path.name, task_id, suffix)
        if dest.exists():
            raise GitError(f"worktree path already exists: {dest}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            await run_git(repo_path, "worktree", "add", "--detach", str(dest), base_commit)
        except GitError as exc:
            raise GitError(f"could not create detached worktree: {exc}") from exc
        return WorkspaceInfo(dest, base_commit, None, True)

    async def create_branch_worktree(
        self, repo_path: Path, base_commit: str, task_id: int, branch: str
    ) -> WorkspaceInfo:
        """Create the task branch and its worktree. Reuses an existing worktree
        for the same branch if one is already registered (re-runs)."""
        existing = await self.find_worktree_for_branch(repo_path, branch)
        if existing:
            return WorkspaceInfo(existing, base_commit, branch, False)
        if not await self.ensure_branch_available(repo_path, branch):
            raise GitError(f"branch {branch!r} already exists but has no worktree")
        dest = self._dest(repo_path.name, task_id, "")
        if dest.exists():
            raise GitError(f"worktree path already exists: {dest}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            await run_git(
                repo_path, "worktree", "add", "-b", branch, str(dest), base_commit
            )
        except GitError as exc:
            raise GitError(f"could not create worktree: {exc}") from exc
        return WorkspaceInfo(dest, base_commit, branch, False)

    async def find_worktree_for_branch(
        self, repo_path: Path, branch: str
    ) -> Path | None:
        try:
            out = await run_git(repo_path, "worktree", "list", "--porcelain")
        except GitError:
            return None
        current: dict[str, str] = {}
        for line in out.splitlines():
            if line.startswith("worktree "):
                current["path"] = line[len("worktree ") :]
            elif line.startswith("branch "):
                current["branch"] = line[len("branch ") :]
            elif line.startswith("detached"):
                current["branch"] = "detached"
            elif line == "":
                if current.get("branch") == f"refs/heads/{branch}":
                    return Path(current["path"])
                current = {}
        return None

    # ------------------------------------------------------- commit / inspect

    async def commit_all(self, worktree: Path, message: str) -> str:
        await run_git(worktree, "add", "-A")
        await run_git(worktree, "commit", "-m", message)
        return (await run_git(worktree, "rev-parse", "HEAD")).strip()

    async def diff(self, worktree: Path, base_commit: str) -> dict:
        """Return {'stat': ..., 'full': ...} of base_commit..HEAD."""
        stat = await run_git(worktree, "diff", "--stat", f"{base_commit}..HEAD")
        full = await run_git(worktree, "diff", f"{base_commit}..HEAD")
        return {"stat": stat, "full": full}

    async def list_commits(self, worktree: Path, base_commit: str) -> list[dict]:
        out = await run_git(
            worktree, "log", f"{base_commit}..HEAD", "--pretty=format:%h|%s|%an"
        )
        commits = []
        for line in out.splitlines():
            if "|" in line:
                sha, subject, author = line.split("|", 2)
                commits.append({"sha": sha, "subject": subject, "author": author})
        return commits

    async def status(self, worktree: Path) -> str:
        return await run_git(worktree, "status", "--porcelain")

    async def head_commit(self, worktree: Path) -> str:
        return (await run_git(worktree, "rev-parse", "HEAD")).strip()

    # ---------------------------------------------------------------- cleanup

    async def remove_worktree(self, repo_path: Path, dest: Path, branch: str | None) -> None:
        """Remove a worktree and (optionally) its task branch. Idempotent-ish:
        missing worktrees are reported, never fatal."""
        if dest.exists():
            try:
                await run_git(repo_path, "worktree", "remove", "--force", str(dest))
            except GitError:
                try:
                    await run_git(dest, "worktree", "remove", "--force", str(dest))
                except GitError as exc:
                    raise GitError(f"could not remove worktree: {exc}") from exc
        if branch:
            try:
                await run_git(repo_path, "branch", "-D", branch)
            except GitError:
                pass

    # ---------------------------------------------------------------- display

    async def worktree_list(self, repo_path: Path) -> list[dict]:
        """JSON-ish listing of all worktrees for the repo (for the UI)."""
        out = await run_git(repo_path, "worktree", "list", "--porcelain")
        return _parse_worktree_list(out)


def _parse_worktree_list(out: str) -> list[dict]:
    entries: list[dict] = []
    current: dict[str, str] = {}
    for line in out.splitlines():
        if line.startswith("worktree "):
            current["path"] = line[len("worktree ") :]
        elif line.startswith("branch "):
            current["branch"] = line[len("branch ") :]
        elif line.startswith("HEAD "):
            current["head"] = line[len("HEAD ") :]
        elif line == "":
            if current:
                entries.append(current)
                current = {}
    if current:
        entries.append(current)
    return entries
