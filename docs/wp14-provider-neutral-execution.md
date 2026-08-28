# WP14 — Provider-Neutral Execution and MCP Engineering Control

## Status

WP14 removes Gemini/ACP from SceneWorks' execution substrate while preserving
Gemini CLI as the default and best-integrated worker.

The architectural rule is:

```text
model provider != agent backend != execution runtime
```

A Gemini authentication failure may make the Gemini worker unavailable. It must
not make SceneWorks project management, Git worktrees, direct MCP repository
inspection, commands, processes, tests, logs or Git operations unavailable.

## Architecture

```text
                         SceneWorks
                             |
       +---------------------+----------------------+
       |                     |                      |
       v                     v                      v
  ChatGPT / MCP         Agent routing        Execution runtime
  reasoning/control          |                      |
       |             +-------+--------+             |
       |             |       |        |             |
       |          Gemini  OpenCode OpenHands        |
       |            ACP   headless    SDK           |
       |                                      native runtime
       +----------------------+----------------------+
                              |
                       EngineeringSession
                              |
                     isolated Git worktree
                              |
              files / commands / processes / Git
```

### Model provider

Owns model inference. Examples: Gemini, OpenAI, Anthropic, local/OpenAI-compatible
providers configured behind a compatible agent runtime.

SceneWorks does not treat a raw model API as a coding agent automatically.

### AgentBackend

An autonomous worker that implements `run()`, `cancel()`, and `health()`.
Transport is deliberately unspecified.

Current adapters:

| Backend | Transport | WP14 status | Intended use |
|---|---|---|---|
| `gemini_acp` | Gemini CLI ACP | supported, default | all qualified roles |
| `opencode` | `opencode run` headless CLI | backup | write-capable coding/delegation |
| `openhands` | SDK/Agent Server/CLI | experimental | optional alternative |
| `fake` | in-process script | tests only | deterministic qualification |

ACP is therefore one adapter transport, not a SceneWorks requirement.

### ExecutionRuntime

A model-free machine capability interface owned by SceneWorks.

WP14 adds `native`, implementing:

- UTF-8 file list/read/search/write;
- SHA-256 optimistic concurrency for writes;
- argument-based command execution;
- persistent child process start/output/stop;
- Git status/diff/commit;
- worktree path and symlink confinement for filesystem operations.

The runtime contains no model reasoning and never calls Gemini/OpenCode/OpenHands
to perform a primitive operation.

## EngineeringSession

`EngineeringSession` is separate from the legacy Gemini `AgentSession`.

An EngineeringSession persists:

- project id;
- pinned base commit;
- dedicated `sw/mcp-<id>` branch;
- dedicated `<repo>-sw-mcp-<id>` worktree;
- runtime key;
- permission set;
- default delegated backend/model;
- lifecycle status.

The host worktree path is internal and is never returned by MCP.

### Why it is separate from AgentSession

A Gemini provider session represents provider conversation state plus its
worktree. A direct EngineeringSession represents a SceneWorks workspace and
machine authority with no model requirement. Combining them would couple
restart/recovery/cancellation semantics to Gemini again.

Legacy `sceneworks.agent_session.*` tools remain available in Advanced mode for
backward compatibility and are labelled as Gemini-provider sessions.

## MCP modes

### Observe

Semantic read-only SceneWorks state. No execution or mutation.

### Standard

Observe plus governed task/role/workflow actions and:

```text
sceneworks.register_project
```

`register_project` accepts a repository path that already exists on the
**SceneWorks host**. It validates that path as a Git repository and is idempotent
for an already registered resolved path.

This allows a remote ChatGPT client using the tunnel to register PCS by path,
provided SceneWorks itself can access that path.

### Advanced

Standard plus direct EngineeringSession capabilities:

```text
sceneworks.engineering_session.list
sceneworks.engineering_session.get
sceneworks.engineering_session.create
sceneworks.engineering_session.close

sceneworks.workspace.list
sceneworks.workspace.read
sceneworks.workspace.search
sceneworks.workspace.write

sceneworks.command.run

sceneworks.process.start
sceneworks.process.output
sceneworks.process.stop

sceneworks.git.status
sceneworks.git.diff
sceneworks.git.commit

sceneworks.agent.delegate
```

The external MCP client can therefore inspect, edit, run tests, start PCS,
consume stdout/stderr and inspect the actual Git diff without invoking any
backend model.

## Remote worktree creation

"Remote" refers to the MCP caller, not the filesystem operation.

```text
ChatGPT
   | HTTPS/tunnel MCP call
   v
SceneWorks host
   | local git worktree add
   v
configured worktree_root
```

`sceneworks.engineering_session.create(project_id=...)` causes the SceneWorks
host to resolve the project's configured base branch/commit and create the
worktree locally. ChatGPT never mounts or creates the filesystem itself.

This works from a phone/web ChatGPT session as long as:

1. the updated SceneWorks backend is running;
2. the MCP tunnel is connected;
3. MCP is in Advanced mode;
4. the registered project path is accessible to the SceneWorks process;
5. Git can create a worktree under `worktree_root`.

## Process control and PCS

Persistent process control is intentionally separate from one-shot shell work.
Typical flow:

```text
register PCS project (once)
        |
create EngineeringSession
        |
process.start PCS executable / dev launcher
        |
process.output -> startup/log output
        |
workspace.read/search or command.run tests
        |
git.diff
        |
process.stop / restart
```

A process id is internal to the running SceneWorks `native` runtime. MCP verifies
that its cwd belongs to the requesting EngineeringSession before reading or
stopping it.

Process registries are in memory. If SceneWorks itself restarts, the persistent
EngineeringSession/worktree survives but process handles do not. Recreate the
process after restart.

## Agent delegation

Direct control and autonomous workers are independent.

`sceneworks.agent.delegate` runs a registered `AgentBackend` inside the existing
EngineeringSession worktree and persists a normal taskless `Execution` for
status/evidence.

Example choices:

```text
backend=gemini_acp   # default worker
backend=opencode     # independent non-ACP backup
backend=openhands    # optional/experimental
```

The client polls `sceneworks.get_execution` and then inspects
`sceneworks.git.diff`. Agent prose does not replace Git evidence.

## OpenCode backup

WP14 adds an OpenCode adapter specifically so backup execution does not depend
on ACP.

SceneWorks invokes the documented headless shape:

```text
opencode run --auto --dir <worktree> [--model provider/model] [--agent name] <prompt>
```

Provider authentication and provider/model configuration remain owned by
OpenCode.

WP14 deliberately limits the adapter to write-capable coding/delegation roles.
Headless automatic approval does not provide the same per-tool read-only
mediation as Gemini ACP. A future OpenCode policy adapter may widen this after
qualification; WP14 does not pretend the enforcement is equivalent.

## Backend and model settings

The Settings page exposes:

- backend health/status;
- default worker;
- Gemini executable/model;
- OpenCode executable/provider-model/agent;
- `strongest`, `coding`, and `research` profile routes;
- MCP mode and direct EngineeringSession permission ceiling.

A role asks for a provider-neutral `model_profile`. `ModelRouter` resolves it to
a concrete backend/model when an execution is created and persists that exact
resolution on the `Execution` row.

Changing settings rewires the live `ExecutionEngine`, workflow router and Git
services; the UI must not report a setting as applied while long-lived services
continue using stale objects.

## Failure and fallback policy

Gemini remains default, but provider failure is no longer platform failure.

WP14 does **not** silently transfer a partially mutated autonomous execution to a
second agent. Safe behavior is:

- before work starts: select/reroute explicitly to another healthy backend;
- direct MCP runtime: continue working even if all model backends are down;
- after an agent has mutated the worktree: preserve the worktree and surface the
  failed execution; the supervisor may inspect the diff and deliberately resume
  or delegate to another backend.

This prevents a hidden failover from compounding an unknown partial change.

## Security boundary

### Filesystem

SceneWorks rejects absolute paths, `..` escapes after resolution, and symlink
resolutions outside the EngineeringSession worktree for direct file operations.
Writes support `expected_sha256` so an MCP client can avoid overwriting a file
that changed since it was read.

### Commands/processes

`command.run` uses executable + argument vectors, not shell-string evaluation.
The cwd is confined to the worktree.

This is **not an OS sandbox**. A permitted executable can access anything the OS
user can access and can potentially use the network. `network_access` cannot be
treated as a hard independent boundary once arbitrary process execution is
granted; use an OS/container/firewall boundary if that distinction is required.

### Git

EngineeringSession Git tools operate in the session worktree. Cleanup refuses a
dirty worktree. Closing with cleanup preserves the branch/commits and removes
only the clean checked-out worktree.

### MCP transport

The bare SceneWorks MCP endpoint has no built-in user authentication. Keep it on
localhost and use the trusted tunnel, or place authenticated TLS infrastructure
in front of a remote deployment.

## Explicit non-goals for WP14

- Reimplement Gemini web search/fetch or native subagents.
- Make OpenCode permission semantics look equivalent to ACP when they are not.
- Automatic cross-provider failover after mutations.
- OS/container sandboxing.
- Remote worker/file synchronization; execution still occurs on the SceneWorks
  host.
- Replace the governed Task workflow with raw MCP operations.

## Qualification

Deterministic WP14 tests cover:

- path confinement and stale-write rejection;
- direct command and persistent process execution;
- project registration through MCP;
- EngineeringSession branch/worktree creation and cleanup;
- direct workspace read and command invocation through MCP;
- OpenCode unavailable/read-only boundary behavior.

The repository CI remains the final gate: backend non-live suite,
provider-independent qualification and frontend production build.
