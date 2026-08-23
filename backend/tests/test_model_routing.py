"""WP8 model-profile routing regression tests."""

from __future__ import annotations

import asyncio

import pytest

from app.agents.base import AgentResult
from app.agents.model_routing import ModelRouter, ModelRoutingError
from app.agents.registry import BackendRegistry
from app.config.settings import ModelProfileRoute
from app.models import Execution
from app.roles.definitions import RoleDefinition


def _role(*, backend: str = "gemini_acp", profile: str | None = "strongest") -> RoleDefinition:
    return RoleDefinition(
        key="test-role",
        display_name="Test",
        description="test",
        backend=backend,
        model_profile=profile,
    )


def test_profile_route_resolves_backend_and_model(settings):
    settings.model_profile_routes = {
        "strongest": ModelProfileRoute(backend="fake", model="routed-model")
    }
    resolution = ModelRouter(settings, {"fake", "gemini_acp"}).resolve(_role())

    assert resolution.profile == "strongest"
    assert resolution.backend == "fake"
    assert resolution.model == "routed-model"
    assert resolution.source == "profile_route"


def test_unmapped_profile_uses_backend_configured_default(settings):
    settings.model_profile_routes = {}
    settings.gemini_model = "configured-default"
    resolution = ModelRouter(settings, {"gemini_acp"}).resolve(_role())

    assert resolution.backend == "gemini_acp"
    assert resolution.model == "configured-default"
    assert resolution.source == "backend_default"


def test_route_to_unknown_backend_fails_before_execution(settings):
    settings.model_profile_routes = {
        "strongest": ModelProfileRoute(backend="not-registered", model="x")
    }
    with pytest.raises(ModelRoutingError, match="unregistered backend"):
        ModelRouter(settings, {"fake", "gemini_acp"}).resolve(_role())


def test_backend_registry_binds_gemini_model_to_execution_scoped_settings(settings):
    registry = BackendRegistry(settings, include_fake=False, include_openhands=False)
    proxy = registry.get("gemini_acp")
    target = proxy._target("gemini-routed")  # noqa: SLF001 - boundary regression

    assert target is not proxy
    assert target._settings.gemini_model == "gemini-routed"  # noqa: SLF001
    assert target._settings.gemini_environment["GEMINI_MODEL"] == "gemini-routed"  # noqa: SLF001


def test_backend_registry_binds_openhands_model_even_if_environment_differs(settings, monkeypatch):
    monkeypatch.setenv("SCENEWORKS_OPENHANDS_MODEL", "environment-model")
    registry = BackendRegistry(settings, include_fake=False, include_openhands=True)
    proxy = registry.get("openhands")
    target = proxy._target("execution-model")  # noqa: SLF001 - boundary regression

    assert target._model() == "execution-model"  # noqa: SLF001


async def test_service_execution_persists_resolved_backend_and_model(context):
    context.settings.model_profile_routes = {
        "strongest": ModelProfileRoute(backend="fake", model="persisted-model")
    }
    role = context.roles.effective("architect")

    execution = await context.workflow.create_execution(
        task=None,
        project=None,
        role=role,
        workspace={},
        system_prompt="system",
        user_prompt="user",
    )

    assert execution.model_profile == "strongest"
    assert execution.backend == "fake"
    assert execution.model_name == "persisted-model"

    # Changing configuration after queue time must not rewrite the execution.
    context.settings.model_profile_routes["strongest"] = ModelProfileRoute(
        backend="gemini_acp", model="later-model"
    )
    async with context.engine_factory() as session:
        persisted = await session.get(Execution, execution.id)
        assert persisted is not None
        assert persisted.backend == "fake"
        assert persisted.model_name == "persisted-model"


async def test_engine_transports_persisted_model_to_backend_request(context):
    class CapturingBackend:
        key = "fake"
        label = "capture"

        def __init__(self):
            self.request = None

        async def run(self, request, workspace, event_sink):
            self.request = request
            return AgentResult(status="completed", summary="done")

        async def cancel(self, execution_id: str) -> None:
            return None

        async def health(self):  # pragma: no cover - not needed by this test
            raise AssertionError("health should not be called")

    capture = CapturingBackend()
    context.backends.register("fake", capture)

    execution = Execution(
        id="wp8-capture",
        task_id=None,
        role="engineer",
        backend="fake",
        model_profile="coding",
        model_name="immutable-model",
        status="QUEUED",
        workspace={},
        system_prompt="system",
        user_prompt="user",
    )
    async with context.engine_factory() as session:
        session.add(execution)
        await session.commit()

    await context.execution_engine.start(execution.id)
    deadline = asyncio.get_running_loop().time() + 5
    while context.execution_engine.is_active(execution.id):
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("execution did not finish")
        await asyncio.sleep(0.01)

    assert capture.request is not None
    assert capture.request.model_profile == "coding"
    assert capture.request.model == "immutable-model"
