"""Backend health must not report deterministic local backends as down."""

from __future__ import annotations

import asyncio

from app.agents.fake import FakeAgentBackend
from app.agents.registry import BackendRegistry


async def test_cold_health_reports_fake_available_immediately(settings):
    registry = BackendRegistry(settings, include_fake=True, include_openhands=False)

    health = await registry.health_all()
    by_key = {item.key: item for item in health}

    assert by_key["fake"].available is True
    assert by_key["fake"].version == "fake-1.0"
    assert by_key["gemini_acp"].available is False
    assert by_key["gemini_acp"].detail == "probing..."

    # Do not leak the scheduled provider probe into the next test.
    if registry._refresh_task is not None:
        registry._refresh_task.cancel()
        try:
            await registry._refresh_task
        except asyncio.CancelledError:
            pass


async def test_forced_backend_endpoint_returns_real_fake_health(client, context):
    # Isolate this endpoint test from machine-dependent provider availability.
    context.backends._backends = {"fake": FakeAgentBackend()}
    context.backends._health_cache = None
    context.backends._health_checked_at = 0.0

    response = await client.get("/api/backends?refresh=true")
    assert response.status_code == 200, response.text
    assert response.json() == [
        {
            "key": "fake",
            "label": "Fake (scripted)",
            "available": True,
            "version": "fake-1.0",
            "detail": None,
        }
    ]
