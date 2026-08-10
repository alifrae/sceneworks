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
| OpenHands | Experimental (unvalidated) | `SCENEWORKS_OPENHANDS_MODEL` |
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

### Gemini ACP vs OpenHands comparison

| Criterion | Gemini ACP | OpenHands |
|---|---|---|
| Protocol | ACP v1 over stdio | SDK/WebSocket, REST API (HTTP), or CLI |
| Setup | Install Gemini CLI + authenticate | Deploy Agent Server or install CLI |
| Permission model | SceneWorks mediates all I/O via ACP proxy | Workspace confinement |
| File access control | Per-request approval/rejection | Directory scoping |
| Shell control | ACP terminal proxy | Agent-side configuration |
| Event granularity | Structured ACP update notifications | WebSocket streaming, polling, or stdout |
| Resource footprint | Single process per execution | Server + agent processes |
| Reliability | Tied to Gemini CLI stability | Tied to Agent Server availability |
| Authentication | Gemini CLI's own auth | API key or session key |
| Status | Validated (default) | Experimental / unvalidated |
| Recommended for | Direct integration, permission enforcement | Teams, cloud deployment, shared infrastructure |

OpenHands execution modes (tried in order):
1. **SDK/WebSocket** (preferred): official `openhands-sdk` with WebSocket streaming
2. **HTTP polling** (compatibility fallback): REST API, polls for status
3. **CLI/headless** (development fallback only): one-off subprocess

**Strengths of Gemini ACP:**
- Fine-grained permission enforcement at the protocol level
- No separate server to manage
- Structured event streaming via ACP updates
- Battle-tested with Gemini CLI 0.53.x

**Strengths of OpenHands:**
- Multi-agent, multi-backend architecture
- Can run on dedicated infrastructure (team sharing)
- Browser-based UI for conversation history
- Supports multiple LLM providers

**Limitations — OpenHands:**
- Requires a running Agent Server (additional deployment)
- Workspace isolation depends on server configuration — OpenHands workspace
  confinement is configured at the Agent Server level, not enforced per-request
  by SceneWorks as Gemini ACP does. SceneWorks passes the exact worktree path,
  but cannot guarantee the Agent Server respects directory boundaries.
- Event polling adds latency vs streaming (HTTP mode)
- API is evolving (version differences may require adaptation)

**Limitations — Gemini ACP:**
- Single-machine execution only
- ACP protocol is tightly coupled to Gemini CLI
- No built-in conversation persistence beyond SceneWorks
- Windows requires console window for shell tool

**Recommended usage:**
- Use **Gemini ACP** for development on a single machine with fine-grained
  permission control.
- Use **OpenHands** for team-shared infrastructure, multi-LLM support, or
  when you need the Agent Canvas UI for conversation review.
- Both can coexist — assign different roles to different backends as needed.
  For example: Architect on Gemini ACP (strict read-only), Engineer on
  OpenHands (more flexible shell access).
