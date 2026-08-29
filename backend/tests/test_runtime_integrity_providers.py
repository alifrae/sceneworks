"""Focused provider/runtime integrity regressions for the Windows repair."""

from __future__ import annotations

import asyncio
import json
import os

import pytest

from app.agents.gemini_acp_attachments import AttachmentAwareGeminiACPBackend
from app.agents.integrity import IntegrityBackendRegistry
from app.agents.openhands import _MODULE_CACHE, _module_available
from app.agents.opencode import OpenCodeBackend


async def _raise_not_implemented(*args, **kwargs):
    raise NotImplementedError()


async def test_gemini_subprocess_probe_failure_keeps_exception_type(
    settings, monkeypatch
) -> None:
    backend = AttachmentAwareGeminiACPBackend(settings)
    monkeypatch.setattr(backend, "_launch_command", lambda: ["gemini"])
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _raise_not_implemented)

    registry = IntegrityBackendRegistry(
        settings, include_fake=False, include_openhands=False
    )
    registry._backends = {"gemini_acp": backend}
    health = await registry.health_all(force=True)

    assert len(health) == 1
    assert health[0].available is False
    assert "NotImplementedError" in (health[0].detail or "")
    assert len(health[0].detail or "") <= 400
    await registry.shutdown()


async def test_opencode_subprocess_probe_failure_keeps_exception_type(
    settings, monkeypatch
) -> None:
    backend = OpenCodeBackend(settings)
    monkeypatch.setattr(backend, "_executable", lambda: "opencode")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _raise_not_implemented)

    registry = IntegrityBackendRegistry(
        settings, include_fake=False, include_openhands=False
    )
    registry._backends = {"opencode": backend}
    health = await registry.health_all(force=True)

    assert len(health) == 1
    assert health[0].available is False
    assert "NotImplementedError" in (health[0].detail or "")
    assert len(health[0].detail or "") <= 400
    await registry.shutdown()


async def test_repository_diagnostics_redact_registered_host_root(
    context, git_repo, monkeypatch
) -> None:
    async def fail_probe(*args, **kwargs):
        raise RuntimeError(f"git probe failed below {git_repo}")

    monkeypatch.setattr(context.git, "_run", fail_probe)
    snapshot = await context.git.repository_snapshot(git_repo, "main")
    encoded = json.dumps(snapshot)

    assert snapshot["availability"]["state"] == "unavailable"
    assert snapshot["diagnostic"]["exception_type"] == "RuntimeError"
    assert str(git_repo) not in encoded
    assert "<registered-repository>" in encoded


@pytest.mark.openhands
def test_normal_sync_openhands_modules_are_detectable_when_required() -> None:
    if os.environ.get("SCENEWORKS_EXPECT_OPENHANDS") != "1":
        pytest.skip("OpenHands extra is qualified by the dedicated Windows runtime job")

    _MODULE_CACHE.clear()
    assert _module_available("openhands.sdk") is True
    assert _module_available("openhands.tools.preset.default") is True
