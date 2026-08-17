"""OpenHands backend tests (WP2.5).

Two layers:

- **Offline tests** (the bulk) need no OpenHands install and no server. They
  cover mode resolution, health honesty, the event mapping, cancellation wiring
  and every defect WP2.5 found.
- **Live tests** are marked `live` + `openhands` and skip cleanly unless a real
  OpenHands install *and* a reachable LLM endpoint are configured. They never
  fall back to FakeAgentBackend, so an unavailable provider can never appear as
  a pass.

The pre-WP2.5 suite had 9 tests, none of which exercised any execution path —
they covered only the unconfigured and error branches, which is why the adapter's
incompatibility with the real SDK went unnoticed.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.agents.base import AgentEventSink, AgentRequest, Workspace
from app.agents.openhands import (
    MODE_CLI,
    MODE_HTTP,
    MODE_LOCAL,
    MODE_REMOTE,
    MODE_UNCONFIGURED,
    VALIDATED_MODES,
    OpenHandsBackend,
    _message_text,
    _summarize,
    warm_module_cache,
)
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
        super().__init__("test-oh", None, lambda *a, **k: _noop())
        self.events: list[tuple[str, dict, str]] = []

    async def emit(self, type: str, payload: dict, severity: str = "info") -> None:
        self.events.append((type, payload, severity))

    def types(self) -> list[str]:
        return [t for t, _, _ in self.events]


async def _noop():
    pass


@pytest.fixture(autouse=True)
def _isolate_openhands_env(monkeypatch, request):
    """Never let the developer's own OpenHands configuration steer a test.

    Exempt for `live`-marked tests: those exist precisely to use the real
    configuration, and stripping it made them fail with "no model configured"
    rather than actually exercising OpenHands.
    """
    if request.node.get_closest_marker("live") is not None:
        return
    for var in (
        "SCENEWORKS_OPENHANDS_URL",
        "SCENEWORKS_OPENHANDS_BASE_URL",
        "SCENEWORKS_OPENHANDS_EXECUTABLE",
        "SCENEWORKS_OPENHANDS_MODEL",
        "SCENEWORKS_OPENHANDS_API_KEY",
        "SCENEWORKS_OPENHANDS_MODE",
        "OH_SESSION_API_KEYS_0",
    ):
        monkeypatch.delenv(var, raising=False)


def sdk_installed() -> bool:
    warm_module_cache()
    from app.agents.openhands import _MODULE_CACHE

    return bool(_MODULE_CACHE.get("openhands.sdk")) and bool(
        _MODULE_CACHE.get("openhands.tools.preset.default")
    )


# ------------------------------------------------------------ contract tests


async def test_key_identity(tmp_path):
    backend = OpenHandsBackend(make_settings(tmp_path))
    assert backend.key == "openhands"
    assert "OpenHands" in backend.label


async def test_label_carries_no_blanket_unvalidated_banner(tmp_path):
    """Validation status belongs in health() and the docs, not in the label.

    The old label embedded "[EXPERIMENTAL / UNVALIDATED ...]", which was both
    unactionable in the UI and wrong once a mode had been validated. health()
    now reports which mode is active and whether it is validated.
    """
    backend = OpenHandsBackend(make_settings(tmp_path))
    assert "UNVALIDATED" not in backend.label


async def test_openhands_backend_in_registry(context):
    assert "openhands" in context.backends.keys()
    backend = context.backends.get("openhands")
    assert backend.key == "openhands"


async def test_openhands_is_not_default_backend(context):
    """WP2.5 policy: Gemini ACP stays the default even if OpenHands validates."""
    from app.roles.definitions import default_roles

    for role in default_roles():
        assert role.backend == "gemini_acp", (
            f"role {role.key} must default to gemini_acp; openhands is opt-in"
        )
    assert context.settings.default_backend != "openhands"


async def test_health_all_includes_openhands(context):
    keys = {h.key for h in await context.backends.health_all(force=True)}
    assert {"fake", "gemini_acp", "openhands"} <= keys


# ------------------------------------------------------- mode resolution


async def test_mode_is_unconfigured_without_sdk_server_or_executable(tmp_path, monkeypatch):
    """With nothing available the backend must say so, not guess a mode."""
    monkeypatch.setattr(
        "app.agents.openhands._module_available", lambda name: False
    )
    monkeypatch.setattr("app.agents.openhands.shutil.which", lambda name: None)
    backend = OpenHandsBackend(make_settings(tmp_path))

    resolution = backend.resolve_mode()
    assert resolution.mode == MODE_UNCONFIGURED
    assert "not importable" in resolution.detail or "no SDK" in resolution.detail


async def test_server_url_with_sdk_resolves_to_remote(tmp_path, monkeypatch):
    monkeypatch.setattr("app.agents.openhands._module_available", lambda name: True)
    backend = OpenHandsBackend(
        make_settings(tmp_path, openhands_url="http://localhost:8000")
    )
    assert backend.resolve_mode().mode == MODE_REMOTE


async def test_server_url_without_sdk_resolves_to_http(tmp_path, monkeypatch):
    monkeypatch.setattr("app.agents.openhands._module_available", lambda name: False)
    backend = OpenHandsBackend(
        make_settings(tmp_path, openhands_url="http://localhost:8000")
    )
    resolution = backend.resolve_mode()
    assert resolution.mode == MODE_HTTP
    assert "SDK is not usable" in resolution.detail


async def test_sdk_without_server_resolves_to_local(tmp_path, monkeypatch):
    """The mode WP2.5 validated: in-process, no Agent Server needed.

    The pre-WP2.5 adapter had no local mode at all — SDK execution required
    SCENEWORKS_OPENHANDS_URL, so the one path that works on this machine was
    unreachable.
    """
    monkeypatch.setattr("app.agents.openhands._module_available", lambda name: True)
    backend = OpenHandsBackend(make_settings(tmp_path))

    resolution = backend.resolve_mode()
    assert resolution.mode == MODE_LOCAL
    assert resolution.validated is True


async def test_mismatched_tools_package_does_not_resolve_to_local(tmp_path, monkeypatch):
    """REGRESSION: openhands-tools importing but mismatched must not look usable.

    openhands-tools 1.42.1 installs cleanly against openhands-sdk 1.17.0 and then
    raises ModuleNotFoundError during import. `find_spec` reported it present,
    which is why the old adapter believed SDK mode was available.
    """
    def probe(name: str) -> bool:
        return name == "openhands.sdk"  # tools import fails

    monkeypatch.setattr("app.agents.openhands._module_available", probe)
    monkeypatch.setattr("app.agents.openhands.shutil.which", lambda name: None)
    backend = OpenHandsBackend(make_settings(tmp_path))

    resolution = backend.resolve_mode()
    assert resolution.mode == MODE_UNCONFIGURED
    assert resolution.sdk_available is True
    assert resolution.tools_available is False
    assert "openhands-tools not importable" in resolution.detail


async def test_executable_only_resolves_to_cli(tmp_path, monkeypatch):
    monkeypatch.setattr("app.agents.openhands._module_available", lambda name: False)
    backend = OpenHandsBackend(
        make_settings(tmp_path, openhands_executable="openhands")
    )
    assert backend.resolve_mode().mode == MODE_CLI


async def test_mode_can_be_forced_and_a_bad_value_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr("app.agents.openhands._module_available", lambda name: True)
    forced = OpenHandsBackend(make_settings(tmp_path, openhands_mode="http"))
    assert forced.resolve_mode().mode == MODE_HTTP

    bad = OpenHandsBackend(make_settings(tmp_path, openhands_mode="teleport"))
    resolution = bad.resolve_mode()
    assert resolution.mode == MODE_UNCONFIGURED
    assert "unknown SCENEWORKS_OPENHANDS_MODE" in resolution.detail


async def test_only_local_mode_is_marked_validated():
    """Documentation-as-code: unvalidated modes must not claim validation."""
    assert VALIDATED_MODES == {MODE_LOCAL}


# --------------------------------------------------------------- health


async def test_health_unavailable_when_nothing_is_configured(tmp_path, monkeypatch):
    monkeypatch.setattr("app.agents.openhands._module_available", lambda name: False)
    monkeypatch.setattr("app.agents.openhands.shutil.which", lambda name: None)
    backend = OpenHandsBackend(make_settings(tmp_path))

    health = await backend.health()
    assert health.available is False


async def test_health_is_unavailable_without_a_model(tmp_path, monkeypatch):
    """REGRESSION: config alone is not health.

    LLM() without a model raises a pydantic ValidationError deep in the SDK,
    which surfaced as an opaque "OpenHands SDK error". Health must catch this as
    a configuration problem up front.
    """
    monkeypatch.setattr("app.agents.openhands._module_available", lambda name: True)
    backend = OpenHandsBackend(make_settings(tmp_path))

    health = await backend.health()
    assert health.available is False
    assert "no model configured" in (health.detail or "")


async def test_health_reports_mode_and_model_when_usable(tmp_path, monkeypatch):
    monkeypatch.setattr("app.agents.openhands._module_available", lambda name: True)
    backend = OpenHandsBackend(
        make_settings(tmp_path, openhands_model="lm_studio/some-model")
    )

    health = await backend.health()
    assert health.available is True
    assert f"mode={MODE_LOCAL}" in (health.detail or "")
    assert "lm_studio/some-model" in (health.detail or "")


async def test_health_fails_when_the_llm_endpoint_is_unreachable(tmp_path, monkeypatch):
    """A configured but dead endpoint is proof execution will fail."""
    monkeypatch.setattr("app.agents.openhands._module_available", lambda name: True)
    backend = OpenHandsBackend(make_settings(
        tmp_path,
        openhands_model="lm_studio/some-model",
        # Port 9 is the discard service; nothing serves HTTP there.
        openhands_base_url="http://127.0.0.1:9/v1",
    ))

    health = await backend.health()
    assert health.available is False
    assert "LLM endpoint unreachable" in (health.detail or "")


async def test_health_reports_missing_shell_support_on_this_platform(tmp_path, monkeypatch):
    """Operators must be told the Engineer cannot run, not discover it mid-run."""
    monkeypatch.setattr("app.agents.openhands._module_available", lambda name: True)
    monkeypatch.setattr(
        "app.agents.openhands._shell_supported_locally", lambda: False
    )
    backend = OpenHandsBackend(
        make_settings(tmp_path, openhands_model="lm_studio/some-model")
    )

    health = await backend.health()
    assert health.available is True
    assert "shell UNAVAILABLE" in (health.detail or "")


async def test_remote_health_marks_the_mode_unvalidated(tmp_path, monkeypatch):
    """Remote mode works in principle but was never live-validated; say so."""
    monkeypatch.setattr("app.agents.openhands._module_available", lambda name: True)

    async def ok(self, url):
        return True, "ok"

    monkeypatch.setattr(OpenHandsBackend, "_probe_server", ok)
    backend = OpenHandsBackend(make_settings(
        tmp_path, openhands_url="http://localhost:8000",
        openhands_model="anthropic/some-model",
    ))

    health = await backend.health()
    assert health.available is True
    assert "NOT validated" in (health.detail or "")


# ------------------------------------------------------------------- run


async def test_run_without_configuration_fails_with_the_reason(tmp_path, monkeypatch):
    monkeypatch.setattr("app.agents.openhands._module_available", lambda name: False)
    monkeypatch.setattr("app.agents.openhands.shutil.which", lambda name: None)
    backend = OpenHandsBackend(make_settings(tmp_path))
    sink = RecordingSink()

    result = await backend.run(
        AgentRequest(execution_id="x", role="architect", system_prompt="s", user_prompt="u"),
        Workspace(path=tmp_path, repo_path=tmp_path, permissions=("repository_read",)),
        sink,
    )
    assert result.status == "failed"
    assert "not usable" in (result.error or "") or "not importable" in (result.error or "")


async def test_run_always_reports_the_mode_it_chose(tmp_path, monkeypatch):
    """A backend that silently degrades makes a failed run undiagnosable."""
    monkeypatch.setattr("app.agents.openhands._module_available", lambda name: False)
    monkeypatch.setattr("app.agents.openhands.shutil.which", lambda name: None)
    backend = OpenHandsBackend(make_settings(tmp_path))
    sink = RecordingSink()

    await backend.run(
        AgentRequest(execution_id="x", role="architect", system_prompt="s", user_prompt="u"),
        Workspace(path=tmp_path, repo_path=tmp_path),
        sink,
    )
    mode_events = [p for t, p, _ in sink.events if p.get("name") == "backend.mode"]
    assert mode_events, "the resolved mode must be emitted as an event"
    assert "mode" in mode_events[0]


async def test_run_without_a_model_fails_with_a_clear_message(tmp_path, monkeypatch):
    """REGRESSION: LLM() with no model raised a raw pydantic ValidationError."""
    monkeypatch.setattr("app.agents.openhands._module_available", lambda name: True)
    backend = OpenHandsBackend(make_settings(tmp_path))
    sink = RecordingSink()

    result = await backend.run(
        AgentRequest(execution_id="x", role="architect", system_prompt="s", user_prompt="u"),
        Workspace(path=tmp_path, repo_path=tmp_path, permissions=("repository_read",)),
        sink,
    )
    assert result.status == "failed"
    assert "SCENEWORKS_OPENHANDS_MODEL" in (result.error or "")
    assert "ValidationError" not in (result.error or "")


async def test_shell_role_fails_fast_when_the_platform_has_no_shell(tmp_path, monkeypatch):
    """REGRESSION: the terminal tool raised NotImplementedError mid-run on Windows.

    A role needing shell must be refused up front with the real reason, rather
    than failing deep inside the agent after the model has already been used.
    """
    monkeypatch.setattr("app.agents.openhands._module_available", lambda name: True)
    monkeypatch.setattr(
        "app.agents.openhands._shell_supported_locally", lambda: False
    )
    backend = OpenHandsBackend(
        make_settings(tmp_path, openhands_model="lm_studio/some-model")
    )
    sink = RecordingSink()

    result = await backend.run(
        AgentRequest(execution_id="x", role="engineer", system_prompt="s", user_prompt="u"),
        Workspace(
            path=tmp_path, repo_path=tmp_path,
            permissions=("repository_read", "repository_write", "shell_execute"),
        ),
        sink,
    )
    assert result.status == "failed"
    assert "does not support Windows" in (result.error or "")
    assert "Gemini ACP" in (result.error or ""), "point the operator at a working option"


async def test_read_only_role_is_not_blocked_by_missing_shell(tmp_path, monkeypatch):
    """Read-only roles must remain usable where shell is unavailable."""
    monkeypatch.setattr("app.agents.openhands._module_available", lambda name: True)
    monkeypatch.setattr(
        "app.agents.openhands._shell_supported_locally", lambda: False
    )
    backend = OpenHandsBackend(
        make_settings(tmp_path, openhands_model="lm_studio/some-model")
    )
    sink = RecordingSink()

    result = await backend.run(
        AgentRequest(execution_id="x", role="architect", system_prompt="s", user_prompt="u"),
        Workspace(path=tmp_path, repo_path=tmp_path, permissions=("repository_read",)),
        sink,
    )
    # It may still fail (no real model here), but not for the shell reason.
    assert "does not support Windows" not in (result.error or "")


async def test_sdk_import_failure_does_not_silently_become_http(tmp_path, monkeypatch):
    """REGRESSION: `except ImportError: return await self._run_http(...)`.

    The old adapter fell back to HTTP polling whenever an SDK import failed —
    against a server that may not exist — and told nobody. Failing explicitly is
    the only diagnosable behaviour.
    """
    monkeypatch.setattr("app.agents.openhands._module_available", lambda name: True)
    backend = OpenHandsBackend(
        make_settings(tmp_path, openhands_model="lm_studio/some-model")
    )

    called = {"http": False}

    async def spy_http(self, *args, **kwargs):
        called["http"] = True
        from app.agents.base import AgentResult

        return AgentResult(status="completed", summary="should not happen")

    monkeypatch.setattr(OpenHandsBackend, "_run_http", spy_http)

    import builtins

    real_import = builtins.__import__

    def broken_import(name, *args, **kwargs):
        if name.startswith("openhands"):
            raise ImportError("simulated version mismatch")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", broken_import)

    result = await backend.run(
        AgentRequest(execution_id="x", role="architect", system_prompt="s", user_prompt="u"),
        Workspace(path=tmp_path, repo_path=tmp_path, permissions=("repository_read",)),
        RecordingSink(),
    )

    assert called["http"] is False, "an SDK import failure must not fall back to HTTP"
    assert result.status == "failed"
    assert "versions must match" in (result.error or "")


# ---------------------------------------------------------- event mapping


class _Ev:
    """Stand-in for an SDK event: mapping keys off the class name."""

    def __init__(self, name, **fields):
        self.__class__ = type(name, (_Ev,), {})
        for k, v in fields.items():
            setattr(self, k, v)


def _mk(name, **fields):
    obj = type(name, (), {})()
    for k, v in fields.items():
        setattr(obj, k, v)
    return obj


async def test_message_event_maps_to_agent_message(tmp_path):
    backend = OpenHandsBackend(make_settings(tmp_path))
    sink = RecordingSink()

    block = _mk("TextBlock", text="the add function subtracts")
    message = _mk("Message", content=[block])
    await backend._emit_mapped(_mk("MessageEvent", llm_message=message), sink)

    assert "agent.message" in sink.types()
    assert "subtracts" in sink.events[0][1]["text"]


async def test_action_event_maps_to_tool_started(tmp_path):
    backend = OpenHandsBackend(make_settings(tmp_path))
    sink = RecordingSink()

    await backend._emit_mapped(
        _mk("ActionEvent", tool_name="file_editor", tool_call_id="c1",
            thought="I will read the file", action=_mk("A", path="calc/core.py")),
        sink,
    )
    types = sink.types()
    assert "agent.thought_summary" in types
    assert "tool.started" in types
    assert "file.changed" in types


async def test_terminal_action_maps_to_command_started(tmp_path):
    backend = OpenHandsBackend(make_settings(tmp_path))
    sink = RecordingSink()

    await backend._emit_mapped(
        _mk("ActionEvent", tool_name="terminal", tool_call_id="c2",
            action=_mk("A", command="python check.py")),
        sink,
    )
    assert "command.started" in sink.types()
    started = [p for t, p, _ in sink.events if t == "command.started"][0]
    assert "check.py" in started["command"]


async def test_observation_event_maps_to_tool_completed_and_output(tmp_path):
    backend = OpenHandsBackend(make_settings(tmp_path))
    sink = RecordingSink()

    await backend._emit_mapped(
        _mk("ObservationEvent", tool_name="terminal", tool_call_id="c2",
            observation="all checks passed"),
        sink,
    )
    types = sink.types()
    assert "tool.completed" in types
    assert "command.output" in types


async def test_agent_error_event_maps_to_error_severity(tmp_path):
    backend = OpenHandsBackend(make_settings(tmp_path))
    sink = RecordingSink()

    await backend._emit_mapped(_mk("AgentErrorEvent", error="tool exploded"), sink)

    assert sink.events[0][0] == "agent.event"
    assert sink.events[0][2] == "error"


async def test_unknown_event_is_surfaced_not_dropped(tmp_path):
    """A new SDK event type must be visible, not silently discarded."""
    backend = OpenHandsBackend(make_settings(tmp_path))
    sink = RecordingSink()

    await backend._emit_mapped(_mk("SomeBrandNewEvent", whatever=1), sink)

    assert sink.events[0][0] == "agent.event"
    assert "SomeBrandNewEvent" in sink.events[0][1]["name"]


async def test_openhands_payloads_stay_inside_the_adapter(tmp_path):
    """Only the generic SceneWorks vocabulary may leave this adapter."""
    from app.events.types import EVENT_LABELS

    backend = OpenHandsBackend(make_settings(tmp_path))
    sink = RecordingSink()

    for event in (
        _mk("MessageEvent", llm_message=_mk("M", content="hi")),
        _mk("ActionEvent", tool_name="terminal", action=_mk("A", command="ls")),
        _mk("ObservationEvent", tool_name="terminal", observation="out"),
        _mk("AgentErrorEvent", error="bad"),
        _mk("SystemPromptEvent"),
    ):
        await backend._emit_mapped(event, sink)

    for event_type in sink.types():
        assert event_type in EVENT_LABELS, (
            f"{event_type!r} is not part of the generic SceneWorks event vocabulary"
        )


async def test_summary_comes_from_agent_messages(tmp_path):
    """REGRESSION: the old adapter read `conversation.events`, which does not exist.

    `getattr(conversation, "events", [])` returned an empty list on every real
    LocalConversation, so SDK mode produced no events and always returned the
    placeholder "OpenHands SDK completed."
    """
    collected = [
        _mk("SystemPromptEvent"),
        _mk("MessageEvent", source="agent", llm_message=_mk("M", content="first")),
        _mk("MessageEvent", source="agent", llm_message=_mk("M", content="final answer")),
    ]
    summary = _summarize(collected)
    assert "final answer" in summary
    assert "OpenHands completed without" not in summary


async def test_summary_states_plainly_when_there_was_no_text(tmp_path):
    assert "without producing a textual summary" in _summarize([_mk("SystemPromptEvent")])


async def test_message_text_handles_string_and_block_content(tmp_path):
    assert _message_text(_mk("MessageEvent", llm_message=_mk("M", content="plain"))) == "plain"
    blocks = [_mk("B", text="a"), _mk("B", text="b")]
    assert _message_text(
        _mk("MessageEvent", llm_message=_mk("M", content=blocks))
    ) == "a\nb"


# ------------------------------------------------------------ cancellation


async def test_cancel_is_a_noop_for_an_unknown_execution(tmp_path):
    backend = OpenHandsBackend(make_settings(tmp_path))
    await backend.cancel("nonexistent")  # must not raise


async def test_cancel_calls_pause_then_close_on_the_conversation(tmp_path):
    """REGRESSION: cancellation was decorative.

    The old adapter set an asyncio Event and called `close()`, but `run()` was
    executing synchronously *on the event loop*, so nothing could observe either
    until the run had already finished. `pause()` is the SDK's cooperative stop.
    """
    backend = OpenHandsBackend(make_settings(tmp_path))
    calls: list[str] = []

    conversation = _mk(
        "LocalConversation",
        pause=lambda: calls.append("pause"),
        close=lambda: calls.append("close"),
    )
    backend._conversations["exec-1"] = conversation
    backend._cancel_events["exec-1"] = asyncio.Event()

    await backend.cancel("exec-1")

    assert calls == ["pause", "close"]
    assert backend._cancel_events["exec-1"].is_set()


async def test_cancel_survives_a_raising_conversation(tmp_path):
    backend = OpenHandsBackend(make_settings(tmp_path))

    def boom():
        raise RuntimeError("already closed")

    backend._conversations["exec-2"] = _mk("C", pause=boom, close=boom)
    await backend.cancel("exec-2")  # must not raise


# ------------------------------------------------- import-probe performance


async def test_module_probe_is_cached(tmp_path):
    """REGRESSION: importing openhands.sdk on the event loop stalled workflows.

    The probe imports litellm and opentelemetry and takes seconds. Uncached and
    inline, it froze the API long enough for an in-flight workflow to be
    cancelled. It is now cached and warmed in a worker thread.
    """
    from app.agents import openhands as mod

    mod._MODULE_CACHE.clear()
    calls = {"n": 0}
    real = mod._module_available

    warm_module_cache()
    first = dict(mod._MODULE_CACHE)
    assert set(first) == set(mod._PROBE_MODULES)

    # A second warm must not re-import.
    import builtins

    real_import = builtins.__import__

    def counting_import(name, *a, **k):
        if name.startswith("openhands"):
            calls["n"] += 1
        return real_import(name, *a, **k)

    builtins.__import__ = counting_import
    try:
        warm_module_cache()
    finally:
        builtins.__import__ = real_import
    assert calls["n"] == 0, "cached probe must not re-import"
    assert real is mod._module_available


# ---------------------------------------------------------- runaway bounds


async def test_agent_iterations_are_bounded(tmp_path):
    """REGRESSION: the SDK default of 500 turns let a run consume its whole budget.

    A live read-only task with a small local model ran for 3h52m against the
    unbounded default, burning the entire execution timeout without concluding.
    SceneWorks bounds the turn count so a non-converging model yields a finished
    run with partial output instead of a stall.
    """
    settings = make_settings(tmp_path)
    assert settings.openhands_max_iterations > 0
    assert settings.openhands_max_iterations <= 100, (
        "the bound must be meaningfully lower than the SDK default of 500"
    )


async def test_iteration_bound_is_configurable_and_never_zero(tmp_path):
    settings = make_settings(tmp_path, openhands_max_iterations=7)
    assert settings.openhands_max_iterations == 7
    # The adapter clamps to at least 1 so a misconfigured 0 cannot deadlock.
    assert max(1, int(getattr(settings, "openhands_max_iterations", 40))) == 7


# -------------------------------------------------------------- live tests
#
# Real OpenHands execution. Skipped unless genuinely available — never faked.


def live_config() -> tuple[bool, str]:
    """Whether a real OpenHands run is possible here."""
    import os

    if not sdk_installed():
        return False, "openhands-sdk/openhands-tools not importable"
    if not os.environ.get("SCENEWORKS_OPENHANDS_MODEL"):
        return False, "SCENEWORKS_OPENHANDS_MODEL not set"
    return True, "ok"


requires_live_openhands = pytest.mark.skipif(
    not live_config()[0], reason=f"live OpenHands unavailable: {live_config()[1]}",
)


@pytest.mark.live
@pytest.mark.openhands
@requires_live_openhands
async def test_live_health_reports_available(tmp_path):
    """A configured live install must pass health with a real mode."""
    backend = OpenHandsBackend(make_settings(tmp_path))
    health = await backend.health()

    assert health.available is True, health.detail
    assert health.version, "health must report the installed SDK version"
    assert f"mode={MODE_LOCAL}" in (health.detail or "")


@pytest.mark.live
@pytest.mark.openhands
@requires_live_openhands
async def test_live_read_only_execution_does_not_touch_files(tmp_path, git_repo):
    """Real OpenHands run against a real repo; nothing may change."""
    import subprocess

    from tests.conftest import git

    # A small local model takes minutes for one tool-using turn: measured ~430 s
    # for a single file inspection with gemma-4-e2b via LM Studio. The adapter
    # budget is execution_timeout_seconds * 1.2.
    backend = OpenHandsBackend(
        make_settings(tmp_path, execution_timeout_seconds=1800)
    )
    sink = RecordingSink()
    before = git(git_repo, "rev-parse", "HEAD").strip()

    result = await backend.run(
        AgentRequest(
            execution_id="live-ro",
            role="architect",
            system_prompt="You are a read-only code analyst.",
            user_prompt=(
                "Read app.py in the working directory and state in one sentence "
                "what main() returns. Do not modify any file."
            ),
        ),
        Workspace(
            path=git_repo, repo_path=git_repo, permissions=("repository_read",),
        ),
        sink,
    )

    assert result.status == "completed", result.error
    assert git(git_repo, "rev-parse", "HEAD").strip() == before
    assert not subprocess.run(
        ["git", "status", "--porcelain"], cwd=str(git_repo),
        capture_output=True, text=True,
    ).stdout.strip(), "a read-only role must leave the worktree clean"
    # Real structured events must have reached SceneWorks.
    assert sink.events
    assert any(t == "agent.message" for t in sink.types())
