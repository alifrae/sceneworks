# Agent Backends and Model Routing

SceneWorks separates **model inference**, **autonomous agent workers**, and **machine execution**. These are different layers and should not be treated as interchangeable.

```text
Model provider
Gemini / OpenAI / Anthropic / local / other
        |
        v
AgentBackend
Gemini ACP / OpenCode / OpenHands
        |
        v
SceneWorks control plane
Task workflow or EngineeringSession
        |
        v
ExecutionRuntime / Git / PCS / evidence
```

- A **model provider** produces inference.
- An **AgentBackend** is an optional autonomous worker implementing `run()`, `cancel()` and `health()`.
- An **ExecutionRuntime** is SceneWorks-owned, model-free machine execution for direct engineering sessions.

A model API is not automatically a coding agent. ACP is one adapter transport, not a SceneWorks platform requirement.

## Current backends

| Key | Transport | Status | Intended use |
|---|---|---|---|
| `gemini_acp` | Gemini CLI ACP over stdio | Supported / default | Primary autonomous worker and strongest current per-request mediation |
| `opencode` | `opencode run` headless CLI | Backup | Write-capable coding/delegation using providers configured in OpenCode |
| `openhands` | OpenHands SDK / Agent Server / CLI | Experimental | Optional alternative; not the execution substrate |
| `fake` | in-process scripted backend | Tests only | Deterministic provider-independent qualification |

Gemini CLI is the recommended default autonomous worker. A Gemini authentication/model failure does **not** remove direct MCP workspace, command/process, Git, PCS or GUI-evidence capabilities because those belong to SceneWorks' runtime/services.

## Current routing model

Routing has two configuration edges:

```text
Role -> provider-neutral profile -> backend/model
```

Default role profiles remain part of role definitions:

| Role | Default profile |
|---|---|
| CEO | `strongest` |
| CTO | `strongest` |
| Chief Architect | `strongest` |
| Technical Expert | `strongest` |
| Product | `strongest` |
| Engineer | `coding` |
| Reviewer / QA | `strongest` |
| GTM | `research` |

WP21 adds persisted **role -> profile overrides** in Settings. An override changes only the provider-neutral profile; it does not duplicate a raw provider/model identifier on the role. Clearing the override restores the code-defined default.

`ModelRouter` then resolves the effective profile to an optional concrete backend/model from Settings.

Example profile routes:

```json
{
  "strongest": {"backend": "gemini_acp", "model": null},
  "coding": {"backend": "opencode", "model": "provider/model"},
  "research": {"backend": "gemini_acp", "model": null}
}
```

The Settings page shows:

```text
Role | default profile | effective profile | resolved backend | resolved model | source
```

The concrete backend/model is persisted on the `Execution` when the execution is created. Later Settings changes therefore do not rewrite provenance.

The current profile vocabulary (`strongest`, `coding`, `research`) is still code-defined. User-extensible arbitrary profile creation is not part of WP21.

## No-silent-fallback policy

SceneWorks does not silently hand a partially mutated worktree from one autonomous provider to another.

Safe rules:

1. Before an agent starts, a configured backend may be selected normally.
2. Direct MCP/runtime work remains independent of autonomous provider health.
3. If an autonomous worker fails after mutation, preserve the worktree and inspect the SceneWorks/Git evidence.
4. Delegating the partially changed worktree to another provider must be an explicit decision.

This prevents hidden provider failover from compounding an unknown partial implementation.

## Gemini ACP

`backend/app/agents/gemini_acp.py` is the primary autonomous adapter.

- Gemini CLI is launched in ACP mode.
- SceneWorks mediates ACP client-side file/shell requests according to role policy.
- ACP updates map into SceneWorks' generic execution event vocabulary.
- Provider-native features such as search or subagents remain inside the adapter/provider rather than being cloned into NativeRuntime.
- Provider authentication/model availability remains external to SceneWorks.

Configuration:

```env
SCENEWORKS_GEMINI_EXECUTABLE=gemini
SCENEWORKS_GEMINI_MODEL=
```

An empty model means the backend/provider default is used unless a profile route overrides it.

## OpenCode backup

SceneWorks invokes OpenCode headlessly rather than requiring ACP:

```text
opencode run --auto --dir <worktree> [--model provider/model] [--agent name] <prompt>
```

Provider credentials and catalog configuration remain owned by OpenCode.

Current policy boundary: the headless OpenCode adapter is intended for write-capable coding/delegation. SceneWorks does not claim the same per-tool read-only mediation as Gemini ACP for this adapter.

Configuration:

```env
SCENEWORKS_OPENCODE_EXECUTABLE=opencode
SCENEWORKS_OPENCODE_MODEL=<provider/model>
SCENEWORKS_OPENCODE_AGENT=
```

## OpenHands

OpenHands remains optional and experimental. It is not required for direct SceneWorks execution and is not the preferred backup path for the current architecture.

Existing dependency/platform limitations are recorded in [limitations.md](limitations.md) and the historical [WP2.5 OpenHands validation](wp2.5-openhands-validation.md).

## Direct execution is not an AgentBackend

`backend/app/runtime/native.py` contains no model or prompt loop.

```text
ChatGPT / supervisor
  -> SceneWorks MCP
     -> EngineeringSession
        -> NativeRuntime
           -> workspace
           -> commands/processes
           -> Git
```

PCS lifecycle, GUI evidence, controlled UI Automation and WP21 task-verification projection are SceneWorks services layered on the same governed session/evidence model; they are not autonomous backends either.

When autonomous coding is useful, `sceneworks.agent.delegate` can invoke a configured `AgentBackend` inside the already-governed EngineeringSession worktree.

## Adding an AgentBackend

A new backend adapter should:

- implement the common `run / cancel / health` contract;
- receive generic SceneWorks request/workspace/event types;
- keep provider/protocol-specific objects inside the adapter;
- respect the supplied worktree and state its real confinement strength accurately;
- return explicit failed/cancelled results rather than silently switching providers;
- have deterministic non-live tests;
- expose operator settings only when they are actually useful.

ACP, headless CLI, HTTP and SDK transports are all valid adapter strategies.

## Security note

A worktree or working-directory restriction is not an OS sandbox. Any backend/runtime that can launch arbitrary processes may exercise the operating-system authority of the user running SceneWorks. Use an actual OS/container/network boundary when hostile-code isolation is required.
