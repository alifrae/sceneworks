# Agent Backends

## Architecture: Model Providers vs Agent Backends

SceneWorks separates two layers:

```text
Model Provider
DeepSeek / Gemini / OpenAI / Claude / etc.
        │  (provides text generation)
        ↓
Agent Runtime / Backend
Gemini CLI ACP / OpenHands / future runtime
        │  (provides repo tools, shell, permissions, cancellation, streaming)
        ↓
SceneWorks AgentBackend
```

A raw model API (e.g. OpenAI chat completions) is **not** automatically an
engineering agent. SceneWorks also needs:
- Repository tools (file read/write with path enforcement)
- Shell control and command execution
- Permission enforcement per role
- Cancellation signaling
- Structured event streaming

These are provided by the Agent Backend layer. The model is only one
component of the backend.

## Current backend support

| Backend | Status | Model selection |
|---|---|---|
| Gemini CLI ACP | Supported / default | `SCENEWORKS_GEMINI_MODEL` (unset = CLI auto) |
| OpenHands | **Experimental** — `local` mode live-validated for read-only roles; see [below](#openhands-status-wp25) | `SCENEWORKS_OPENHANDS_MODEL` (required) |
| Fake | Testing only | Scripted (no model) |

**DeepSeek direct**: Not currently a SceneWorks AgentBackend. DeepSeek is a
model provider; SceneWorks needs an agent runtime that wraps it with
filesystem/shell/permission capabilities. DeepSeek via OpenHands is possible
in principle, dependent on OpenHands validation and configuration.

**Provider-neutral runtime**: An OpenAI-compatible agent runtime that
supports arbitrary provider models remains a candidate for a later release.
V3.0 ships with Gemini CLI ACP as the live-validated default.

**`model_profile` is not model selection.** Each role carries a
`model_profile` (`strongest`, `coding`, `research`) and it is copied onto
every execution row, but **no backend reads it** — it is provenance metadata
only. The model actually used comes from the backend's own configuration
(`SCENEWORKS_GEMINI_MODEL`, `SCENEWORKS_OPENHANDS_MODEL`) or, when unset,
from the agent runtime's automatic selection. If you implement a new backend,
you may map `request.model_profile` to a concrete model — nothing does today,
and a profile→model mapping is deferred to V3.1 provider routing.

## Adding a Backend

SceneWorks isolates execution providers behind the `AgentBackend` protocol.
Each backend is a single module that implements `run()`, `cancel()`, and
`health()` and emits only the generic event vocabulary.

### Steps

1. **Create the backend module** at `backend/app/agents/<name>.py`

   Implement the `AgentBackend` protocol from `app/agents/base.py`:

   ```python
   from app.agents.base import (
       AgentBackend, AgentEventSink, AgentRequest,
       AgentResult, BackendHealth, Workspace
   )

   class MyBackend(AgentBackend):
       key = "my_backend"
       label = "My Backend"

       async def run(self, request, workspace, event_sink) -> AgentResult:
           # Execute the agent in the given workspace.
           # Emit events through event_sink.emit().
           # Return AgentResult(status="completed") or status="failed".
           ...

       async def cancel(self, execution_id: str) -> None:
           # Signal cancellation to the running execution.
           ...

       async def health(self) -> BackendHealth:
           # Check availability and version.
           return BackendHealth(key=self.key, label=self.label, ...)
   ```

   **Contract requirements:**
   - `run()` must operate only within the provided `Workspace`
   - Events emitted through `AgentEventSink` must use the vocabulary from
     `app/events/types.py`
   - `run()` must poll `event_sink.cancelled()` periodically to honor
     cancellation
   - `run()` must NOT raise exceptions — failures should be returned as
     `AgentResult(status="failed", error=...)`
   - The backend must not access files outside the workspace or repo root
   - `health()` must be fast (under 10 seconds)

2. **Register the backend** in `backend/app/agents/registry.py`:

   ```python
   from app.agents.my_backend import MyBackend

   class BackendRegistry:
       def __init__(self, settings, ...):
           self._backends = {
               "gemini_acp": GeminiACPBackend(settings),
               "openhands": OpenHandsBackend(settings),
               "my_backend": MyBackend(settings),    # new
           }
   ```

3. **Add settings** in `backend/app/config/settings.py` if the backend
   needs configuration:

   ```python
   class Settings(BaseSettings):
       my_backend_url: str | None = None
       my_backend_api_key: str | None = None
   ```

4. **Point a role at it** in `backend/app/roles/definitions.py`:

   ```python
   RoleDefinition(
       key="engineer",
       backend="my_backend",    # use the new backend
       ...
   )
   ```

   Or set `SCENEWORKS_DEFAULT_BACKEND=my_backend` as the global default.

5. **Add tests** at `backend/tests/test_my_backend.py`:

   Tests must not require a live service. Verify the contract:
   - `health()` returns correct availability when unconfigured
   - `run()` returns `failed` when not configured
   - `cancel()` does not raise on unknown execution IDs
   - Event emission through the sink works correctly

### Backend evaluation

When adding a new backend, evaluate against these criteria:

| Criterion | Description |
|---|---|
| Repository exploration | Can the agent understand the codebase structure? |
| Implementation correctness | Does it produce working, correct code changes? |
| Scope discipline | Does it make minimal, focused changes? |
| Multi-file changes | Can it modify multiple files in a coordinated way? |
| Test execution/debugging | Can it run tests and iterate on failures? |
| Diff quality | Are diffs clean, reviewable, and well-scoped? |
| Cancellation | Does cancellation cleanly terminate the agent? |
| Worktree isolation | Does it stay within the provided workspace? |
| Reliability | How often does it succeed vs fail? |

## OpenHands status (WP2.5)

Validated by WP2.5 against **openhands-sdk 1.17.0 + openhands-tools 1.17.0** on
**Windows 11**, using `local` mode with an OpenAI-compatible LLM endpoint
(LM Studio). Full evidence in
[wp2.5-openhands-validation.md](wp2.5-openhands-validation.md).

**Status: EXPERIMENTAL.** Real execution works, and one mode is live-validated,
but a material capability gap remains on this platform.

| Aspect | Finding |
|---|---|
| `local` mode (no Agent Server) | **Live-validated** for read-only roles |
| Shell / terminal tool | **Unavailable on Windows** — upstream `NotImplementedError` in `openhands/tools/terminal/terminal/factory.py` |
| Engineer role on Windows | **Not possible** in `local` mode (needs shell); refused up front with a clear message |
| `remote` / `http` / `cli` modes | Implemented, **not validated** — no Agent Server was available |
| Install | Requires the `openhands` extra; **openhands-sdk and openhands-tools versions must match** |

### Modes

Resolved explicitly by `OpenHandsBackend.resolve_mode()` and reported as a
`backend.mode` event on every run. There is no silent fallback: a backend that
quietly degrades makes a failed run impossible to diagnose.

| Mode | Requires | Validated |
|---|---|---|
| `local` | `openhands-sdk` + `openhands-tools`, a model | **yes** (read-only roles) |
| `remote` | the above + `SCENEWORKS_OPENHANDS_URL` | no |
| `http` | `SCENEWORKS_OPENHANDS_URL` (no SDK) | no |
| `cli` | `openhands` executable | no |

Override with `SCENEWORKS_OPENHANDS_MODE=local|remote|http|cli`.

### Configuration

```bash
# Install the matched pair (versions must be equal)
cd backend && uv sync --extra openhands

# Required: litellm-form model id
SCENEWORKS_OPENHANDS_MODEL=lm_studio/google/gemma-4-e2b
# LLM endpoint for any OpenAI-compatible server (LM Studio, vLLM, Ollama)
SCENEWORKS_OPENHANDS_BASE_URL=http://127.0.0.1:1234/v1
# Optional: provider credential
SCENEWORKS_OPENHANDS_API_KEY=...
# Optional: Agent Server (switches to remote mode)
SCENEWORKS_OPENHANDS_URL=http://localhost:8000
```

`SCENEWORKS_OPENHANDS_URL` is the **Agent Server**;
`SCENEWORKS_OPENHANDS_BASE_URL` is the **LLM endpoint**. They are different
services — conflating them is why no local validation was previously possible.

`SCENEWORKS_OPENHANDS_MODEL` is **required**: the SDK rejects an unspecified
model, and `health()` reports the backend unavailable without one.

### Health

`health()` deliberately checks more than configuration. It reports the resolved
mode, the SDK version, the model, whether the mode is validated, and whether
shell is available; it probes the LLM endpoint when one is configured and the
Agent Server in remote mode. **An HTTP 200 from `/health` is not treated as
evidence that execution works.**

### Event mapping

OpenHands event payloads never leave the adapter. Mapping to the generic
SceneWorks vocabulary:

| OpenHands event | SceneWorks event(s) |
|---|---|
| `MessageEvent` | `agent.message` |
| `ActionEvent` | `agent.thought_summary`, `tool.started`, plus `command.started` (terminal) or `file.changed` (editor) |
| `ObservationEvent` | `tool.completed`, plus `command.output` (terminal) |
| `AgentErrorEvent` | `agent.event` (severity `error`) |
| `PauseEvent` | `agent.event` (cancellation observed) |
| anything else | `agent.event` with the class name — surfaced, never dropped |

**Not produced, by design:** `test.result` (OpenHands reports no structured test
outcome) and `git.commit` (the agent commits through the shell; SceneWorks
captures the commit itself in `_finish_engineer`). These are absent rather than
fabricated.

### Gemini ACP vs OpenHands

| Criterion | Gemini ACP | OpenHands |
|---|---|---|
| Status | **Supported**, default | **Experimental**, opt-in |
| Protocol | ACP v1 over stdio | openhands-sdk in-process, or REST to an Agent Server |
| Setup | Install Gemini CLI + authenticate | `uv sync --extra openhands` + a model endpoint |
| Permission model | SceneWorks mediates every file/shell request via the ACP proxy | Working-directory scoping only |
| File access control | Per-request approval/rejection | Directory scoping |
| Shell control | ACP terminal proxy | Tool-level; **unavailable on Windows** |
| Event granularity | Structured ACP update notifications | SDK callbacks, mapped by the adapter |
| Streaming | Yes | Yes (via SDK callbacks) |
| Cancellation | ACP cancel + process teardown | `pause()` then `close()` |
| Roles usable on Windows | all | read-only only |
| Validated on | Gemini CLI 0.55.1 | openhands-sdk 1.17.0, `local` mode, Windows 11 |

**Recommended usage.** Gemini ACP for all roles; it is the validated default and
the only backend that can run the Engineer on Windows. OpenHands is worth
enabling for read-only roles if you want a second opinion from a different model
family, or on Linux where its shell tool works. Roles can be mixed — backend is
per-role configuration.

### Known gaps

- **No OS-level sandbox.** In `local` mode the agent runs in the SceneWorks
  process with the worktree as its working directory. Confinement is the tool's
  own path handling, not an OS or container boundary. See
  [limitations.md](limitations.md) for the exact trust boundary.
- **Remote mode has a path-domain problem.** `working_dir` is a path in the
  *server's* filesystem. A remote Agent Server does not see the local SceneWorks
  worktree, so commit-pinned isolation cannot be established the way it is
  locally. This is why remote mode is unvalidated rather than merely untested.
- **Upstream dependency conflict.** Newer openhands-sdk releases (1.42.1 at time
  of writing) currently cannot be installed: the SDK pulls `lmnr`, which pins
  `opentelemetry-semantic-conventions==0.60b1`, while
  `opentelemetry-instrumentation` pins its own matching version and no
  combination satisfies both (pip: `ResolutionImpossible`). The extra is
  therefore pinned to the validated 1.17.0 pair.
- **Installing the extra changes shared dependencies.** It adds ~150 packages and
  moves 6 pre-existing pins, including a **pydantic downgrade** (2.13.4 →
  2.12.5). The full backend suite passes afterwards, but this is why OpenHands is
  an optional extra rather than a default dependency.
