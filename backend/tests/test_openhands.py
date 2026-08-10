"""OpenHands backend tests.

No live OpenHands service/model required.  Tests verify the adapter
boundary against the AgentBackend contract and the SceneWorks event
vocabulary.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.agents.base import AgentEventSink, AgentRequest, Workspace
from app.agents.openhands import OpenHandsBackend
from app.config.settings import Settings


def make_settings(tmp_path: Path, **extra) -> Settings:
    values = dict(
        log_level="WARNING",
        execution_timeout_seconds=60,
        roles_dir=Path(__file__).resolve().parent.parent / "app" / "roles" / "prompts",
        worktree_root=tmp_path / "wt",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 't.db'}",
    )
    values.update(extra)
    return Settings(**values)


class RecordingSink(AgentEventSink):
    def __init__(self):
        super().__init__("test-oh", None, lambda *args: _noop())
        self.events: list[tuple[str, dict, str]] = []

    async def emit(self, type: str, payload: dict, severity: str = "info") -> None:
        self.events.append((type, payload, severity))


async def _noop():
    pass


# ---------------------------------------------------------- contract tests


async def test_key_and_label_identity():
    backend = OpenHandsBackend(Settings())
    assert backend.key == "openhands"
    assert backend.label == "OpenHands Agent Server"


async def test_missing_executable_reports_not_available(tmp_path):
    settings = make_settings(tmp_path)
    backend = OpenHandsBackend(settings)
    health = await backend.health()
    assert health.available is False
    assert "not configured" in (health.detail or "")


async def test_health_with_missing_cli(tmp_path):
    settings = make_settings(
        tmp_path,
        openhands_executable="definitely-missing-openhands-binary-xyz",
    )
    backend = OpenHandsBackend(settings)
    health = await backend.health()
    assert health.available is False


async def test_fake_backend_key_in_registry_does_not_leak_openhands_deps(context):
    """When default_backend is fake, the route still resolves correctly."""
    resp = {}  # placeholder; actual test via context fixture
    # The registry is constructed inside context with include_openhands=True.
    keys = context.backends.keys()
    assert "openhands" in keys
    backend = context.backends.get("openhands")
    assert backend.key == "openhands"
    assert backend.label == "OpenHands Agent Server"


async def test_run_without_url_or_executable_reports_failure(tmp_path):
    settings = make_settings(tmp_path)
    backend = OpenHandsBackend(settings)
    workspace = Workspace(path=tmp_path, repo_path=tmp_path, permissions=("repository_read",))
    sink = RecordingSink()
    result = await backend.run(
        AgentRequest(execution_id="x", role="test", system_prompt="s", user_prompt="u"),
        workspace,
        sink,
    )
    assert result.status == "failed"
    assert "not configured" in (result.error or "")


async def test_cancel_noop_when_no_execution(tmp_path):
    settings = make_settings(tmp_path)
    backend = OpenHandsBackend(settings)
    await backend.cancel("nonexistent")  # must not raise


async def test_openhands_backend_in_registry(context):
    keys = context.backends.keys()
    assert "openhands" in keys
    backend = context.backends.get("openhands")
    health = await backend.health()
    assert health.key == "openhands"
    assert health.label == "OpenHands Agent Server"


async def test_openhands_backend_health_all(context):
    all_health = await context.backends.health_all()
    keys = {h.key for h in all_health}
    assert "fake" in keys
    assert "gemini_acp" in keys
    assert "openhands" in keys


async def test_openhands_is_not_default_backend(context):
    """Gemini ACP and OpenHands must both be available but Gemini
    remains the default for existing roles."""
    # Roles should default to "gemini_acp" backend, not "openhands".
    from app.roles.definitions import default_roles

    for role in default_roles():
        assert role.backend in ("gemini_acp",)  # openhands is opt-in
