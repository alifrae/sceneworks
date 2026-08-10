# Agent Backends

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
| Protocol | ACP v1 over stdio | REST API (HTTP) or CLI |
| Setup | Install Gemini CLI + authenticate | Deploy Agent Server or install CLI |
| Permission model | SceneWorks mediates all I/O via ACP proxy | Workspace confinement |
| File access control | Per-request approval/rejection | Directory scoping |
| Shell control | ACP terminal proxy | Agent-side configuration |
| Event granularity | Structured ACP update notifications | Polling-based or streaming |
| Resource footprint | Single process per execution | Server + agent processes |
| Reliability | Tied to Gemini CLI stability | Tied to Agent Server availability |
| Authentication | Gemini CLI's own auth | API key (optional) |
| Recommended for | Direct integration, permission enforcement | Teams, cloud deployment, shared infrastructure |

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
