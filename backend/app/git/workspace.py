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
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from app.config.settings import Settings

GIT_TIMEOUT_SECONDS = 300

_AGENT_ENV = {
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_PAGER": "cat",
    # Suppress fsmonitor globally for SceneWorks git operations.
    # A managed repo may have core.fsmonitor set (PCS does); every git
    # subprocess that runs inside that repo would then spawn a persistent
    # `git fsmonitor--daemon` process that outlives the parent.  SceneWorks
    # never communicates with the managed repo's own working tree, so the
    # daemon is pure waste — it accumulated to ~308 orphans during a single
    # engineer+reviewer cycle.  These env vars tell Git to skip fsmonitor
    # for this process tree without touching the user's repo config.
    "GIT_CONFIG_PARAMETERS": "'core.fsmonitor=' 'core.useBuiltinFSMonitor=false'",
}


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

    async def _run(self, cwd: Path, *args: str) -> str:
        """Run git with the configured timeout.

        Every git call in this service goes through here so that one setting
        governs them all. `git worktree add` checks out the entire tree; on a
        large repository that comfortably exceeds the old fixed 120s ceiling,
        which aborted worktree creation and failed the task outright.
        """
        return await run_git(
            cwd, *args, timeout=self._settings.git_timeout_seconds
        )

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
            head = await self._run(repo_path, "rev-parse", "--abbrev-ref", "HEAD")
            commit = await self._run(repo_path, "rev-parse", "HEAD")
        except GitError as exc:
            return RepoInfo(repo_path, False, None, None, str(exc))
        branch = head.strip() if head.strip() != "HEAD" else None
        return RepoInfo(repo_path, True, branch, commit.strip() or None)

    async def resolve_base_commit(self, repo_path: Path, branch: str | None) -> str:
        """Resolve the commit a task should be based on (default branch head)."""
        if branch:
            try:
                out = await self._run(repo_path, "rev-parse", "--verify", f"{branch}^{{commit}}")
                return out.strip()
            except GitError:
                pass
        out = await self._run(repo_path, "rev-parse", "HEAD")
        return out.strip()

    async def ensure_branch_available(self, repo_path: Path, branch: str) -> bool:
        """True if the branch does not exist yet (safe to create)."""
        out = await self._run(repo_path, "branch", "--list", branch)
        return out.strip() == ""

    # ------------------------------------------------------- worktree creation

    def _dest_named(self, name: str) -> Path:
        dest = self.worktree_root / name
        if not dest.resolve().is_relative_to(self.worktree_root.resolve()):
            raise GitError("invalid worktree destination (outside worktree root)")
        return dest

    def _dest(self, repo_name: str, task_id: int, suffix: str) -> Path:
        return self._dest_named(f"{repo_name}-sw-task-{task_id}{suffix}")

    async def create_snapshot_worktree(
        self, repo_path: Path, base_commit: str, label: str
    ) -> WorkspaceInfo:
        """Commit-pinned detached worktree not tied to a task.

        Used by manual company asks so that repository-grounded answers read
        committed state only — never the human working tree.
        """
        safe = re.sub(r"[^A-Za-z0-9_.-]", "-", label)[:60]
        dest = self._dest_named(f"{repo_path.name}-sw-ask-{safe}")
        dest = await self._claim_destination(repo_path, dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            await self._run(repo_path, "worktree", "add", "--detach", str(dest), base_commit)
        except GitError as exc:
            raise GitError(f"could not create snapshot worktree: {exc}") from exc
        return WorkspaceInfo(dest, base_commit, None, True)

    async def create_detached_worktree(
        self, repo_path: Path, base_commit: str, task_id: int, suffix: str = "-arch"
    ) -> WorkspaceInfo:
        dest = self._dest(repo_path.name, task_id, suffix)
        dest = await self._claim_destination(repo_path, dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            await self._run(repo_path, "worktree", "add", "--detach", str(dest), base_commit)
        except GitError as exc:
            raise GitError(f"could not create detached worktree: {exc}") from exc
        return WorkspaceInfo(dest, base_commit, None, True)

    async def _claim_destination(self, repo_path: Path, dest: Path) -> Path:
        """Make `dest` usable for a new worktree, or pick a free alternative.

        A directory that git still registers as a worktree is never touched —
        overwriting one would destroy an agent's in-flight work. Only *stale*
        leftovers are reclaimed: git removes a worktree and its registration,
        but on Windows the directory itself often cannot be unlinked while the
        agent process that had it as its cwd is still exiting. That empty
        leftover must not fail the next role, which is what turned a
        recoverable repair iteration into a crashed workflow.
        """
        if await self._is_abandoned_initializing(repo_path, dest):
            # `git worktree add` locks the new worktree with reason
            # "initializing" and unlocks it when it finishes. A run killed
            # mid-checkout (large repository, timeout, restart) leaves that
            # lock forever, and a locked worktree cannot be removed — the path
            # would be poisoned for good. Reclaim it: the checkout never
            # completed, so there is no work to lose.
            try:
                await self._run(repo_path, "worktree", "unlock", str(dest))
            except GitError:
                pass
            try:
                await self.remove_worktree(repo_path, dest, None)
            except GitError:
                pass

        if not dest.exists():
            return dest

        if await self._is_registered_worktree(repo_path, dest):
            raise GitError(f"worktree path already exists: {dest}")

        # Stale leftover: give a just-terminated process time to release its
        # handle, then remove the directory.
        for attempt in range(6):
            shutil.rmtree(str(dest), ignore_errors=True)
            if not dest.exists():
                return dest
            await asyncio.sleep(0.4 * (attempt + 1))
        # Still stuck: use a sibling path rather than failing the workflow.
        for index in range(1, 50):
            candidate = self._dest_named(f"{dest.name}-{index}")
            if not candidate.exists():
                return candidate
        raise GitError(f"could not obtain a free worktree path near {dest}")

    async def _is_abandoned_initializing(self, repo_path: Path, dest: Path) -> bool:
        """True if `dest` is a worktree whose creation never finished.

        Identified by git's own lock reason plus an absent or empty checkout,
        so a healthy worktree — even one briefly locked by a concurrent
        `worktree add` that is still writing files — is never reclaimed.
        """
        if dest.exists() and any(dest.iterdir()):
            return False
        if not await self._is_registered_worktree(repo_path, dest):
            return False
        admin = repo_path / ".git" / "worktrees" / dest.name / "locked"
        try:
            return admin.is_file() and admin.read_text(
                encoding="utf-8", errors="replace"
            ).strip() == "initializing"
        except OSError:
            return False

    async def _is_registered_worktree(self, repo_path: Path, dest: Path) -> bool:
        try:
            entries = await self.worktree_list(repo_path)
        except GitError:
            return False
        target = str(dest.resolve()).lower()
        for entry in entries:
            path = entry.get("path")
            if path and str(Path(path).resolve()).lower() == target:
                return True
        return False

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
            await self._run(
                repo_path, "worktree", "add", "-b", branch, str(dest), base_commit
            )
        except GitError as exc:
            raise GitError(f"could not create worktree: {exc}") from exc
        return WorkspaceInfo(dest, base_commit, branch, False)

    async def find_worktree_for_branch(
        self, repo_path: Path, branch: str
    ) -> Path | None:
        try:
            out = await self._run(repo_path, "worktree", "list", "--porcelain")
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
        await self._run(worktree, "add", "-A")
        await self._run(worktree, "commit", "-m", message)
        return (await self._run(worktree, "rev-parse", "HEAD")).strip()

    async def diff(self, worktree: Path, base_commit: str) -> dict:
        """Return {'stat': ..., 'full': ...} of base_commit..HEAD."""
        stat = await self._run(worktree, "diff", "--stat", f"{base_commit}..HEAD")
        full = await self._run(worktree, "diff", f"{base_commit}..HEAD")
        return {"stat": stat, "full": full}

    async def list_commits(self, worktree: Path, base_commit: str) -> list[dict]:
        out = await self._run(
            worktree, "log", f"{base_commit}..HEAD", "--pretty=format:%h|%s|%an"
        )
        commits = []
        for line in out.splitlines():
            if "|" in line:
                sha, subject, author = line.split("|", 2)
                commits.append({"sha": sha, "subject": subject, "author": author})
        return commits

    async def status(self, worktree: Path) -> str:
        return await self._run(worktree, "status", "--porcelain")

    async def head_commit(self, worktree: Path) -> str:
        return (await self._run(worktree, "rev-parse", "HEAD")).strip()

    # ---------------------------------------------------------------- cleanup

    async def remove_worktree(self, repo_path: Path, dest: Path, branch: str | None) -> None:
        """Remove a worktree and (optionally) its task branch. Idempotent-ish:
        missing worktrees are reported, never fatal."""
        if dest.exists():
            for cwd in (repo_path, dest):
                try:
                    await self._run(cwd, "worktree", "remove", "--force", str(dest))
                    break
                except GitError:
                    continue
            # git may report success while leaving the directory behind (a
            # process still holding it), or fail outright because the parent
            # repository is gone. Either way, delete it directly so nothing
            # leaks under the worktree root.
            if dest.exists():
                shutil.rmtree(str(dest), ignore_errors=True)
            if dest.exists() and any(dest.iterdir()):
                raise GitError(f"could not remove worktree contents: {dest}")
        # Drop any stale registration left behind by a directory-level delete.
        try:
            await self._run(repo_path, "worktree", "prune")
        except GitError:
            pass
        if branch:
            try:
                await self._run(repo_path, "branch", "-D", branch)
            except GitError:
                pass

    # ---------------------------------------------------------------- display

    async def worktree_list(self, repo_path: Path) -> list[dict]:
        """JSON-ish listing of all worktrees for the repo (for the UI)."""
        out = await self._run(repo_path, "worktree", "list", "--porcelain")
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
