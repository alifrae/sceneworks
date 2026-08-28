# Connect SceneWorks to ChatGPT with the MCP plugin

SceneWorks exposes one MCP endpoint with three authority levels. Start in
**Observe**, validate the tunnel/tool catalogue, then raise it deliberately.

```text
Observe  -> semantic reads
Standard -> governed actions + project registration
Advanced -> direct EngineeringSession control + durable evidence + optional workers
```

Gemini CLI remains the default agent worker, but Advanced direct execution and
evidence capture are owned by SceneWorks and do not require a functioning Gemini
model session.

## 1. Start SceneWorks and verify MCP locally

Default endpoint:

```text
http://127.0.0.1:8010/mcp
```

Open or curl it:

```bash
curl http://127.0.0.1:8010/mcp
```

`GET /mcp` is discovery only. ChatGPT uses `POST /mcp` JSON-RPC.

## 2. Connect ChatGPT through Tunnel

For a SceneWorks instance on your laptop/private workstation:

1. Add a custom MCP plugin/integration named `SceneWorks`.
2. Choose the trusted **Tunnel** connection.
3. Set the local target to `http://127.0.0.1:8010/mcp`.
4. Review the tool catalogue before raising SceneWorks above Observe mode.

Do not expose port `8010` directly to the public internet. The bare SceneWorks
service has no user login/OAuth boundary.

For a deliberately remote deployment, terminate authenticated HTTPS before
traffic reaches SceneWorks.

## 3. Observe mode

Configure:

```env
SCENEWORKS_MCP_MODE=observe
```

or use **Settings -> ChatGPT / MCP -> Observe**.

Observe includes semantic tools such as:

```text
sceneworks.list_projects
sceneworks.get_project_context
sceneworks.list_tasks
sceneworks.get_task
sceneworks.get_task_diff
sceneworks.get_execution
sceneworks.search_memory
sceneworks.list_artifacts
```

No worktree, command, process or mutation tools are exposed. `get_task` can still
show evidence summaries from EngineeringSessions that were previously bound to
the task, so objective verification remains readable after authority is lowered.

Example:

```text
@SceneWorks review task <id> using its contract, linked engineering-session evidence and Git truth. Treat agent summaries as inference.
```

## 4. Standard mode

Configure:

```env
SCENEWORKS_MCP_MODE=standard
```

Standard adds governed SceneWorks actions such as task creation/control and role
invocation, plus:

```text
sceneworks.register_project
```

### Register PCS programmatically

If PCS is already cloned on the machine where the SceneWorks backend runs, ask:

```text
@SceneWorks register the Git project at <PCS repository path> as PCS.
```

The path is resolved on the **SceneWorks host**, not on ChatGPT. Registration:

- validates that the path is a Git repository;
- detects the current/default branch when not specified;
- returns the existing project when that resolved path is already registered;
- does not copy or upload the repository through the tunnel.

A Windows path therefore works only when the SceneWorks process itself can
access that path.

## 5. Advanced mode: direct engineering control with evidence

Configure:

```env
SCENEWORKS_MCP_MODE=advanced
```

or choose **Advanced — direct engineering control** in Settings.

Review the capability ceiling. Direct-session permissions include:

```text
repository_read
repository_write
shell_execute
process_control
git_commit
agent_delegate
```

`network_access` is retained as a provider/host capability indicator and
`subagents` exists for legacy Gemini ACP provider sessions. Arbitrary process
execution is not an OS network sandbox: a permitted process can use whatever
network/OS authority the SceneWorks user has.

### Create a task-bound worktree remotely

For assigned work, bind the EngineeringSession to its governed Task:

```text
@SceneWorks create an Advanced engineering session for task 142 with repository read/write, command/process control and agent delegation.
```

ChatGPT calls:

```text
sceneworks.engineering_session.create(project_id=..., task_id=142, ...)
```

SceneWorks validates that the task belongs to the same project, pins the base
commit, and performs `git worktree add` locally on the SceneWorks host, creating
a dedicated branch like:

```text
sw/mcp-<session-id>
```

The absolute worktree path is never returned through MCP. ChatGPT refers to it
by EngineeringSession id.

Task binding is optional for project-level investigations, but use it for
assigned tasks so evidence can be evaluated directly against the task contract.

## 6. Begin an explicit supervisor turn

WP15 groups iterative work into causal turns:

```text
sceneworks.engineering_session.begin_turn
sceneworks.engineering_session.finish_turn
```

Example:

```text
@SceneWorks begin a turn on this session with intent "reproduce the frame-32 playback freeze".
```

The returned `turn_id` should be reused on the direct commands, file operations,
process operations, Git checks or worker delegation caused by that iteration.
Only one turn may be active in a session at once.

A practical sequence is:

```text
turn 1: reproduce
turn 2: investigate
turn 3: implement
turn 4: verify
```

The EngineeringSession/worktree remains the same across those turns.

## 7. Direct workspace tools — no Gemini involved

After creating an EngineeringSession, ChatGPT can use:

```text
sceneworks.workspace.list
sceneworks.workspace.read
sceneworks.workspace.search
sceneworks.workspace.write
```

Direct filesystem operations reject absolute paths and path/symlink escapes.
For safe edits, `workspace.read` returns a SHA-256 and `workspace.write` accepts
`expected_sha256`; a stale edit is rejected instead of silently overwriting a
newer file.

WP15 evidence records file paths and hashes, not another full copy of source
content. Writes record before/after hashes so an edit can be independently
correlated later.

## 8. Run tests or commands directly

Use:

```text
sceneworks.command.run
```

It accepts an executable plus argument list and runs it with cwd inside the
EngineeringSession worktree. It is not a shell-string evaluator.

Command evidence includes executable, arguments, relative cwd, start/finish
time, exit code, bounded stdout/stderr, timeout and cancellation state. A
non-zero exit code is captured as failed evidence rather than being explained
away by an agent.

This path is:

```text
ChatGPT -> MCP -> SceneWorks NativeRuntime -> process
```

not:

```text
ChatGPT -> Gemini -> Gemini terminal tool
```

## 9. Start and control PCS

For a long-lived executable or dev server use:

```text
sceneworks.process.start
sceneworks.process.output
sceneworks.process.stop
```

Process evidence includes the SceneWorks process id, OS PID, executable/args,
start/end time, running/exited state, exit code and bounded stdout/stderr events.

Typical PCS loop:

```text
process.start PCS/dev launcher
        |
process.output -> startup/log output
        |
inspect/edit/run targeted tests
        |
git.diff
        |
process.stop / restart
```

The process id belongs to the running SceneWorks native runtime. SceneWorks
verifies the process cwd belongs to the requesting EngineeringSession before
returning output or stopping it.

If the SceneWorks backend itself restarts, the EngineeringSession/worktree and
already-persisted evidence survive, but in-memory process handles do not. WP15
does not yet provide background durable PCS log capture independent of explicit
process observation.

## 10. Inspect actual Git state

Use:

```text
sceneworks.git.status
sceneworks.git.diff
sceneworks.git.commit
```

`git.diff` compares against the EngineeringSession's pinned base commit and also
returns staged/working-tree state plus changed-file SHA-256 values.

Git remains the canonical diff truth. The evidence ledger stores changed-file
hashes/stat/status and hashes of diff text rather than duplicating the complete
diff into SQLite.

## 11. Inspect evidence independently

WP15 adds:

```text
sceneworks.engineering_session.evidence
sceneworks.engineering_session.events
sceneworks.engineering_session.summary
```

### Evidence

Use `evidence` for the durable action ledger. It can filter by turn/category and
uses `after_id` / `next_after_id` cursoring.

### Events

Use `events` when you want one correlated history. It returns:

- supervisor turns;
- SceneWorks direct-action evidence;
- persisted events from any delegated agent executions, carrying the originating
  `turn_id` and `action_id`.

### Summary

Use `summary` for a high-signal verification view without asking Gemini to
summarize itself. It includes task/base-commit correlation, task acceptance
criteria and required tests, evidence/failure counts, latest actions and changed
file hashes.

Example:

```text
@SceneWorks show the evidence summary for this session and compare it with task 142's required tests and acceptance criteria. Do not use the agent's final answer as proof.
```

## 12. Delegate to Gemini, OpenCode or OpenHands

Direct control does not prevent using coding agents. It makes them optional.

Use:

```text
sceneworks.agent.delegate
```

with the active `turn_id` and an optional backend/model:

```text
backend=gemini_acp   # default
backend=opencode     # non-ACP backup
backend=openhands    # experimental
```

The delegated worker operates in the existing EngineeringSession worktree.
SceneWorks persists a normal `Execution`; its workspace snapshot keeps the
originating turn/action ids, and `engineering_session.events` exposes the
correlated execution event stream.

Provider event text is still inference. After delegation, inspect direct evidence
and `sceneworks.git.diff`.

### When Gemini is broken

If Gemini CLI starts but the model returns an authentication/quota failure:

1. direct workspace/command/process/Git MCP tools still work;
2. choose `opencode` for a delegated coding worker if OpenCode is configured;
3. preserve any partial work already made by a failed agent and inspect its diff
   before deliberately handing the same worktree to another worker.

SceneWorks does not silently cross-provider fail over after mutation.

## 13. OpenCode backup configuration

In Settings, configure **OpenCode — non-ACP backup worker**, or use environment
values:

```env
SCENEWORKS_OPENCODE_EXECUTABLE=opencode
SCENEWORKS_OPENCODE_MODEL=<provider/model>
SCENEWORKS_OPENCODE_AGENT=
```

SceneWorks uses OpenCode headless CLI rather than ACP. OpenCode owns its provider
credentials/provider configuration.

WP14/WP15 limit this adapter to write-capable coding/delegation work because its
headless auto-approval path does not provide Gemini ACP-equivalent per-tool
read-only enforcement.

## 14. Backend and model routing

Settings exposes:

- **Default worker** — Gemini CLI by default;
- backend health;
- Gemini model override;
- OpenCode provider/model;
- model-profile routes for `strongest`, `coding`, and `research`.

Routes are persisted and concrete backend/model selection is recorded on each
Execution so later settings changes do not rewrite execution provenance.

## 15. Legacy Gemini Advanced sessions

WP11 `sceneworks.agent_session.*` tools remain for compatibility. They represent
a persistent Gemini ACP provider conversation and are labelled **legacy Gemini
ACP provider sessions**.

For general ChatGPT-controlled engineering, prefer task-bound
`EngineeringSession` + turns + direct runtime/evidence tools. The generic
`engineering_session.events` surface is the provider-neutral replacement for
relying on a Gemini session's final answer.

## Security boundary

Before enabling Advanced:

- keep SceneWorks local/private or behind authenticated TLS;
- grant only needed EngineeringSession permissions;
- understand that path confinement is not an OS sandbox;
- understand that command/process execution has the SceneWorks user's OS
  authority and can potentially access the network;
- review Git diff before integration;
- keep integration/acceptance explicit.

## Troubleshooting

### ChatGPT cannot reach SceneWorks

Confirm SceneWorks is running, `GET http://127.0.0.1:8010/mcp` works locally and
the ChatGPT tunnel is connected.

### New WP15 tools do not appear

The plugin tool schema comes from the running SceneWorks instance. Run the WP15
code, reconnect/refresh the tunnel/plugin, and rescan tools after changing MCP
mode.

### I can read but cannot register PCS

Project registration requires Standard or Advanced mode.

### I can register PCS but cannot create a worktree

EngineeringSession creation requires Advanced mode and a writable configured
`worktree_root` accessible to the SceneWorks host.

### A turn id is rejected

The turn belongs to another EngineeringSession or is already finished. Begin a
new turn instead of appending new actions to a closed iteration.

### A direct command is rejected

The EngineeringSession lacks `shell_execute`, or the command/cwd is invalid.
Persistent processes require `process_control` separately.

### OpenCode is unavailable

Install/configure the OpenCode executable and its own provider credentials, then
refresh backend health. SceneWorks does not store those provider secrets.

See [WP14 provider-neutral execution](../wp14-provider-neutral-execution.md) for
the execution architecture and [WP15 evidence ledger](../wp15-evidence-ledger.md)
for the evidence/authority contract.
