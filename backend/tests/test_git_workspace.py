"""Git worktree service integration tests (temporary repositories)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.config.settings import Settings
from app.git.workspace import GitError, GitWorktreeService
from tests.conftest import git, require_git


def asyncio_run(coro):
    return asyncio.run(coro)


require_git()


@pytest.fixture
async def svc(settings: Settings) -> GitWorktreeService:
    return GitWorktreeService(settings)


def test_repo_info_git_repo(git_repo: Path, svc: GitWorktreeService):
    info = asyncio_run(svc.repo_info(git_repo))
    assert info.is_git
    assert info.head_branch == "main"
    assert info.head_commit


def test_repo_info_not_a_repo(tmp_path: Path, svc: GitWorktreeService):
    info = asyncio_run(svc.repo_info(tmp_path))
    assert not info.is_git
    assert info.error


def test_worktree_creation_branch_commit_diff_cleanup(git_repo: Path, svc: GitWorktreeService):
    base = asyncio_run(svc.resolve_base_commit(git_repo, "main"))
    branch = "sw-task-42"
    ws = asyncio_run(svc.create_branch_worktree(git_repo, base, 42, branch))
    assert ws.worktree_path.is_dir()
    assert ws.branch == branch

    # The human working tree must stay untouched by default.
    assert (git_repo / "README.md").read_text() == "# Test repo\n"
    assert "sw-task-42" in git(git_repo, "branch", "--list", "sw-task-42")

    # Make changes in the worktree and commit.
    (ws.worktree_path / "newfile.txt").write_text("hello\n", encoding="utf-8")
    commit = asyncio_run(svc.commit_all(ws.worktree_path, "task 42: add newfile"))
    assert commit

    commits = asyncio_run(svc.list_commits(ws.worktree_path, base))
    assert len(commits) == 1
    assert commits[0]["sha"] == commit[:7]

    diff = asyncio_run(svc.diff(ws.worktree_path, base))
    assert "newfile.txt" in diff["stat"]

    # Cleanup removes worktree and branch.
    asyncio_run(svc.remove_worktree(git_repo, ws.worktree_path, branch))
    assert not ws.worktree_path.exists()
    assert git(git_repo, "branch", "--list", branch).strip() == ""

    # Human tree still intact.
    assert (git_repo / "README.md").read_text() == "# Test repo\n"


def test_worktree_detached_read_only_flow(git_repo: Path, svc: GitWorktreeService):
    base = asyncio_run(svc.resolve_base_commit(git_repo, "main"))
    ws = asyncio_run(svc.create_detached_worktree(git_repo, base, 7))
    assert ws.detached
    assert ws.branch is None
    assert asyncio_run(svc.head_commit(ws.worktree_path)) == base
    # Detached worktrees are cheap and removable without branch cleanup.
    asyncio_run(svc.remove_worktree(git_repo, ws.worktree_path, None))


def test_existing_worktree_reused(git_repo: Path, svc: GitWorktreeService):
    base = asyncio_run(svc.resolve_base_commit(git_repo, "main"))
    ws1 = asyncio_run(svc.create_branch_worktree(git_repo, base, 99, "sw-task-99"))
    ws2 = asyncio_run(svc.create_branch_worktree(git_repo, base, 99, "sw-task-99"))
    assert ws1.worktree_path == ws2.worktree_path


def test_refuses_existing_destination(git_repo: Path, svc: GitWorktreeService):
    base = asyncio_run(svc.resolve_base_commit(git_repo, "main"))
    asyncio_run(svc.create_detached_worktree(git_repo, base, 5))
    with pytest.raises(GitError):
        asyncio_run(svc.create_detached_worktree(git_repo, base, 5))


def test_reclaims_stale_leftover_directory(git_repo: Path, svc: GitWorktreeService):
    """A directory git no longer tracks must not block the next worktree.

    On Windows `git worktree remove` regularly succeeds while leaving the
    (now empty) directory behind, because the agent process that had it as its
    cwd is still exiting. Treating that leftover as a fatal collision crashed
    the second review iteration of the auto-repair loop.
    """
    base = asyncio_run(svc.resolve_base_commit(git_repo, "main"))
    first = asyncio_run(svc.create_detached_worktree(git_repo, base, 7, suffix="-review"))
    asyncio_run(svc.remove_worktree(git_repo, first.worktree_path, None))

    # Recreate the bare directory: git does not know about it any more.
    first.worktree_path.mkdir(parents=True, exist_ok=True)
    assert first.worktree_path.exists()

    second = asyncio_run(svc.create_detached_worktree(git_repo, base, 7, suffix="-review"))
    assert second.worktree_path.is_dir()
    assert (second.worktree_path / "README.md").is_file()


def test_remove_worktree_tolerates_leftover_empty_directory(
    git_repo: Path, svc: GitWorktreeService
):
    base = asyncio_run(svc.resolve_base_commit(git_repo, "main"))
    ws = asyncio_run(svc.create_detached_worktree(git_repo, base, 8, suffix="-arch"))
    asyncio_run(svc.remove_worktree(git_repo, ws.worktree_path, None))
    # Idempotent: removing again (or removing a bare leftover) must not raise.
    ws.worktree_path.mkdir(parents=True, exist_ok=True)
    asyncio_run(svc.remove_worktree(git_repo, ws.worktree_path, None))


def test_worktree_root_isolated(git_repo: Path, tmp_path: Path):
    settings = Settings(
        worktree_root=tmp_path / "outside",
        default_backend="fake",
    )
    svc = GitWorktreeService(settings)
    base = asyncio_run(svc.resolve_base_commit(git_repo, "main"))
    ws = asyncio_run(svc.create_branch_worktree(git_repo, base, 1, "sw-task-1"))
    assert str(ws.worktree_path).startswith(str(tmp_path / "outside"))


def test_dirty_main_tree_does_not_block_worktrees(git_repo: Path, svc: GitWorktreeService):
    # Uncommitted changes in the human tree must not leak into worktrees.
    (git_repo / "dirty.txt").write_text("uncommitted", encoding="utf-8")
    base = asyncio_run(svc.resolve_base_commit(git_repo, "main"))
    ws = asyncio_run(svc.create_branch_worktree(git_repo, base, 3, "sw-task-3"))
    assert not (ws.worktree_path / "dirty.txt").exists()
    asyncio_run(svc.remove_worktree(git_repo, ws.worktree_path, "sw-task-3"))


def test_naming_convention(git_repo: Path, svc: GitWorktreeService):
    base = asyncio_run(svc.resolve_base_commit(git_repo, "main"))
    ws = asyncio_run(svc.create_branch_worktree(git_repo, base, 42, "sw-task-42"))
    assert ws.worktree_path.name == f"{git_repo.name}-sw-task-42"
    asyncio_run(svc.remove_worktree(git_repo, ws.worktree_path, "sw-task-42"))


def test_snapshot_isolation_uncommitted_change_not_in_worktree(
    git_repo: Path, svc: GitWorktreeService,
):
    """Regression: an uncommitted change in the human repo must not appear
    in a detached worktree pinned to the earlier commit."""
    repo_file = git_repo / "app.py"
    committed_content = repo_file.read_text(encoding="utf-8")

    base = asyncio_run(svc.resolve_base_commit(git_repo, "main"))
    ws = asyncio_run(svc.create_detached_worktree(git_repo, base, 50))

    try:
        # Modify the original repo file (uncommitted)
        repo_file.write_text(committed_content + "\n# uncommitted change", encoding="utf-8")

        # Read from the worktree — should be the committed version
        worktree_file = ws.worktree_path / "app.py"
        assert worktree_file.exists()
        worktree_content = worktree_file.read_text(encoding="utf-8")
        assert worktree_content == committed_content
        assert "# uncommitted" not in worktree_content
    finally:
        asyncio_run(svc.remove_worktree(git_repo, ws.worktree_path, None))
        repo_file.write_text(committed_content, encoding="utf-8")
