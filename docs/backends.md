# Agent Backends

## Three separate layers

SceneWorks deliberately separates model inference, autonomous agent workers and
machine execution:

```text
Model Provider
Gemini / OpenAI / Anthropic / local / other provider
        |
        v
AgentBackend
Gemini ACP / OpenCode headless / OpenHands
        |
        v
SceneWorks-owned ExecutionRuntime
filesystem / command / process / Git inside an EngineeringSession
```

These layers are not interchangeable.

- A **model provider** produces inference.
- An **AgentBackend** is an autonomous worker implementing `run()`, `cancel()`
  and `health()`.
- An **ExecutionRuntime** provides model-free machine primitives used by direct
  MCP EngineeringSessions.

A raw model API is therefore not automatically a SceneWorks coding agent, and an
agent protocol such as ACP is not required by SceneWorks as a platform.

See [WP14 provider-neutral execution](wp14-provider-neutral-execution.md) for the
direct runtime/MCP architecture.

## Current backends

| Key | Transport | Status | Intended use |
|---|---|---|---|
| `gemini_acp` | Gemini CLI ACP over stdio | **Supported / default** | Primary worker; strongest permission mediation and current qualification baseline |
| `opencode` | `opencode run` headless CLI | **Backup** | Write-capable coding/delegation using providers configured in OpenCode; no ACP dependency |
| `openhands` | OpenHands SDK / Agent Server / CLI | **Experimental** | Optional alternative; current SceneWorks pin/qualification remains limited |
| `fake` | in-process scripted backend | Tests only | Deterministic provider-independent qualification |

Gemini CLI remains the recommended default. The important WP14 change is that a
Gemini model/authentication failure no longer removes SceneWorks' direct MCP
workspace, command/process or Git capabilities.

## Model routing

Roles carry provider-neutral intent profiles such as:

```text
strongest
coding
research
```

`ModelRouter` resolves a profile to an optional concrete backend/model when an
Execution is created. That concrete selection is persisted on the Execution so
later setting changes cannot rewrite provenance.

Example:

```json
{
  "strongest": {"backend": "gemini_acp", "model": null},
  "coding": {"backend": "opencode", "model": "provider/model"},
  "research": {"backend": "gemini_acp", "model": null}
}
```

Routes can be configured from the Settings page or
`SCENEWORKS_MODEL_PROFILE_ROUTES`.

## Gemini ACP

`backend/app/agents/gemini_acp.py` remains the primary adapter.

Characteristics:

- Gemini CLI is launched in ACP mode.
- SceneWorks mediates ACP file/shell requests according to role policy.
- ACP/session updates map to the generic SceneWorks event vocabulary.
- Gemini provider-native capabilities such as web/search/subagents stay inside
  this backend; SceneWorks does not duplicate them in the native runtime.
- Gemini authentication/model availability is external to SceneWorks.

Configuration:

```env
SCENEWORKS_GEMINI_EXECUTABLE=gemini
SCENEWORKS_GEMINI_MODEL=
```

An empty model value leaves model selection to Gemini CLI unless a profile route
or execution-specific model overrides it.

## OpenCode backup

WP14 adds `backend/app/agents/opencode.py` as an independent non-ACP worker.

SceneWorks invokes the documented headless form:

```text
opencode run --auto --dir <worktree> [--model provider/model] [--agent name] <prompt>
```

Provider credentials and provider catalog configuration remain owned by
OpenCode. SceneWorks persists only non-sensitive operational choices.

Configuration:

```env
SCENEWORKS_OPENCODE_EXECUTABLE=opencode
SCENEWORKS_OPENCODE_MODEL=<provider/model>
SCENEWORKS_OPENCODE_AGENT=
```

### Current OpenCode policy boundary

WP14 intentionally restricts this adapter to workspaces that grant
`repository_write`.

Headless `--auto` does not provide the same SceneWorks-controlled per-tool
read-only mediation as the Gemini ACP proxy. SceneWorks therefore does not use
this adapter for read-only roles and does not claim permission parity.

This makes OpenCode a useful coding/delegation backup without weakening a role
that is supposed to be read-only.

## OpenHands status

OpenHands remains optional and **experimental** in WP14. WP14 does not upgrade or
requalify it; the existing pinned integration is kept intact while the new native
ExecutionRuntime removes any need to make OpenHands the SceneWorks execution
substrate.

Current SceneWorks qualification baseline:

- `openhands-sdk==1.17.0`
- `openhands-tools==1.17.0`
- local mode previously validated for read-only roles on the original Windows
  qualification environment;
- remote/http/cli modes implemented but not qualified as production backups;
- current dependency/version limitations remain documented in
  [limitations.md](limitations.md) and
  [wp2.5-openhands-validation.md](wp2.5-openhands-validation.md).

Configuration remains:

```env
SCENEWORKS_OPENHANDS_MODEL=<litellm-model>
SCENEWORKS_OPENHANDS_BASE_URL=<model-endpoint>
SCENEWORKS_OPENHANDS_URL=<optional-agent-server>
SCENEWORKS_OPENHANDS_MODE=local
```

Do not confuse `OPENHANDS_BASE_URL` (model endpoint) with `OPENHANDS_URL`
(OpenHands Agent Server).

## Direct execution is not an AgentBackend

WP14's `backend/app/runtime/native.py` is intentionally not another autonomous
agent. It contains no prompt/model loop.

Direct MCP flow:

```text
ChatGPT
  |
  v
SceneWorks MCP
  |
  v
EngineeringSession
  |
  v
NativeRuntime
  +-- workspace read/search/write
  +-- command.run
  +-- process start/output/stop
  +-- Git status/diff/commit
```

This path remains functional if every configured agent model is unavailable.

When autonomous implementation is useful, `sceneworks.agent.delegate` invokes a
registered AgentBackend in the already-created EngineeringSession worktree.

## Fallback policy

SceneWorks does not silently transfer partially mutated work between autonomous
providers.

Safe rules:

1. Before an autonomous execution starts, the operator/router may select another
   configured backend.
2. Direct MCP runtime work can continue independently of provider health.
3. If an agent fails after mutating the worktree, preserve the worktree, inspect
   the actual Git diff, and deliberately decide whether to resume or delegate to
   another backend.

This avoids compounding an unknown partial change through invisible failover.

## Adding an AgentBackend

Implement `AgentBackend` from `backend/app/agents/base.py` in one adapter module.
The adapter must:

- implement `run()`, `cancel()` and `health()`;
- receive only generic `AgentRequest`, `Workspace`, and `AgentEventSink` types;
- keep provider/protocol-specific objects inside the adapter;
- emit only SceneWorks event vocabulary;
- respect the provided worktree and clearly document its actual confinement and
  permission strength;
- return failed/cancelled `AgentResult`s rather than leaking routine provider
  failures through the engine;
- include deterministic tests that do not need a paid/live provider;
- update Settings/operator documentation when selectable.

ACP, HTTP, SDK and headless CLI adapters are all valid. Do not introduce an ACP
requirement into `AgentBackend`.

## Adding an ExecutionRuntime

Implement `ExecutionRuntime` under `backend/app/runtime/`.

Runtime implementations must:

- contain no model reasoning or prompt loop;
- treat MCP paths as untrusted;
- enforce their documented EngineeringSession boundaries;
- associate persistent processes with the owning session/worktree;
- bound returned output;
- describe OS/container limitations accurately.

A new runtime is useful for a materially different execution environment (for
example a future container/remote worker). It should not exist merely to clone a
provider's native agent tools.

## Backend evaluation

Evaluate a new autonomous backend against:

| Criterion | Question |
|---|---|
| Repository exploration | Can it understand the codebase without leaking outside its allowed workspace? |
| Correctness | Does it produce working, reviewable changes? |
| Scope discipline | Does it respect the requested boundary? |
| Test/debug loop | Can it run targeted verification and react to failures? |
| Permission strength | What is actually enforced, not merely requested in a prompt? |
| Cancellation | Can SceneWorks terminate or cooperatively stop it? |
| Event quality | Is execution observable without provider-specific leakage? |
| Reliability | How often does it complete vs fail? |
| Portability | Does SceneWorks depend on provider-specific protocol semantics outside the adapter? |

## Security note

Neither a worktree nor a working-directory restriction is an OS sandbox. Any
backend/runtime that can launch arbitrary processes may exercise the operating
system authority of the user running SceneWorks. Use an actual container/OS
sandbox or network boundary when hostile-code isolation is required.
