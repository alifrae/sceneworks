"""Shared test fixtures.

Tests never require a live Gemini CLI: the default backend is the scripted
FakeAgentBackend (settings.default_backend="fake").
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config.settings import Settings
from app.context import build_context
from app.main import create_app

BACKEND_DIR = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def deterministic_acp_cancellation(request, monkeypatch):
    """Keep the mock ACP prompt in-flight for the cancellation regression test.

    The old test relied on a fixed 100 ms sleep. Fast Linux runners could
    complete the mock prompt before cancellation arrived, turning a valid
    cancellation test into a scheduler race. The mock now has an explicit
    hold mode; activate it only for that scenario so the test exercises a
    genuinely in-flight request without slowing production code or the suite.
    """
    if request.node.name == "test_mock_acp_cancellation":
        monkeypatch.setenv("MOCK_ACP_HOLD_PROMPT", "1")


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        worktree_root=tmp_path / "worktrees",
        attachment_root=tmp_path / "attachments",
        roles_dir=BACKEND_DIR / "app" / "roles" / "prompts",
        default_backend="fake",
        log_level="WARNING",
        execution_timeout_seconds=120,
        cancel_grace_seconds=2,
        cors_origins=["http://test"],
        checkpoint_db_path=str(tmp_path / "checkpoints.db"),
        max_review_iterations=3,
    )


@pytest.fixture
async def context(settings):
    ctx = await build_context(settings)
    yield ctx
    await ctx.shutdown()


@pytest.fixture
async def client(context):
    app = create_app(settings=context.settings, context=context)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ------------------------------------------------------------------- git repo


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        env={"GIT_TERMINAL_PROMPT": "0"},
        timeout=60,
    )
    if result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stderr}")
    return result.stdout


@pytest.fixture
def git_repo(tmp_path) -> Path:
    """A temporary Git repository with one commit on 'main'."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "test@sceneworks.local")
    git(repo, "config", "user.name", "SceneWorks Test")
    (repo / "README.md").write_text("# Test repo\n", encoding="utf-8")
    (repo / "app.py").write_text("def main():\n    return 1 + 1\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "initial commit")
    return repo


def require_git() -> None:
    if shutil.which("git") is None:
        pytest.skip("git not available")
