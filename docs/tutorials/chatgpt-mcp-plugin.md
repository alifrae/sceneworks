# Connect SceneWorks to ChatGPT with the MCP plugin

SceneWorks exposes one MCP endpoint with three authority levels. Start in
**Observe**, validate the tunnel/tool catalogue, then raise it deliberately.

```text
Observe  -> semantic reads
Standard -> governed actions + project registration
Advanced -> direct EngineeringSession control + optional agent delegation
```

Gemini CLI remains the default agent worker, but Advanced direct execution is
owned by SceneWorks and does not require a functioning Gemini model session.

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

No worktree, command, process or mutation tools are exposed.

Example:

```text
@SceneWorks review project <project> and summarize active work using accepted memory, task state, diffs and execution evidence.
```

## 4. Standard mode

Configure:

```env
SCENEWORKS_MCP_MODE=standard
```

Standard adds governed SceneWorks actions such as task creation/control and role
invocation. WP14 also adds:

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

A Windows path therefore works only when the SceneWorks process itself is
running on Windows and can access that path. A SceneWorks instance in a Linux
container/remote machine cannot see an arbitrary Windows host path unless it is
mounted/shared into that environment.

## 5. Advanced mode: direct engineering control

Configure:

```env
SCENEWORKS_MCP_MODE=advanced
```

or choose **Advanced — direct engineering control** in Settings.

Review the capability ceiling. WP14 direct-session permissions are:

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

### Create a worktree remotely

Ask:

```text
@SceneWorks create an Advanced engineering session for PCS with repository read/write, command/process control, Git commit and agent delegation.
```

ChatGPT calls:

```text
sceneworks.engineering_session.create
```

SceneWorks then performs `git worktree add` **locally on the SceneWorks host**,
creating a dedicated branch like:

```text
sw/mcp-<session-id>
```

under the configured SceneWorks worktree root.

The absolute worktree path is never returned through MCP. ChatGPT refers to it
by EngineeringSession id.

## 6. Direct workspace tools — no Gemini involved

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

Example:

```text
@SceneWorks search this engineering session for the PCS startup entry point and show the relevant files before changing anything.
```

## 7. Run tests or commands directly

Use:

```text
sceneworks.command.run
```

It accepts an executable plus argument list and runs it with cwd inside the
EngineeringSession worktree. It is not a shell-string evaluator.

Example:

```text
@SceneWorks run the targeted backend tests in this engineering session and show stdout/stderr.
```

This path is:

```text
ChatGPT -> MCP -> SceneWorks NativeRuntime -> process
```

not:

```text
ChatGPT -> Gemini -> Gemini terminal tool
```

A Gemini authentication/model failure therefore does not prevent direct command
execution.

## 8. Start and control PCS

For a long-lived executable or dev server use:

```text
sceneworks.process.start
sceneworks.process.output
sceneworks.process.stop
```

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

If the SceneWorks backend itself restarts, the EngineeringSession/worktree
persists but in-memory process handles do not. Start the process again.

## 9. Inspect actual Git state

Use:

```text
sceneworks.git.status
sceneworks.git.diff
sceneworks.git.commit
```

`git.diff` compares against the EngineeringSession's pinned base commit and also
returns staged/working-tree state. Prefer this over trusting an agent's prose
summary.

Cleanup refuses to delete a dirty worktree. Closing with clean-worktree cleanup
preserves the session branch/commits.

## 10. Delegate to Gemini, OpenCode or OpenHands

Direct control does not prevent using coding agents. It makes them optional.

Use:

```text
sceneworks.agent.delegate
```

with an optional backend/model:

```text
backend=gemini_acp   # default
backend=opencode     # non-ACP backup
backend=openhands    # experimental
```

The delegated worker operates in the existing EngineeringSession worktree.
SceneWorks persists a normal `Execution`; poll:

```text
sceneworks.get_execution
```

and then inspect:

```text
sceneworks.git.diff
```

### When Gemini is broken

If Gemini CLI starts but the model returns an authentication/quota failure:

1. direct workspace/command/process/Git MCP tools still work;
2. choose `opencode` for a delegated coding worker if OpenCode is configured;
3. preserve any partial work already made by a failed agent and inspect its diff
   before handing the same worktree to another autonomous worker.

WP14 does not silently cross-provider fail over after mutation.

## 11. OpenCode backup configuration

In Settings, configure **OpenCode — non-ACP backup worker**, or use environment
values:

```env
SCENEWORKS_OPENCODE_EXECUTABLE=opencode
SCENEWORKS_OPENCODE_MODEL=<provider/model>
SCENEWORKS_OPENCODE_AGENT=
```

SceneWorks uses OpenCode headless CLI rather than ACP. OpenCode owns its provider
credentials/provider configuration.

WP14 limits this adapter to write-capable coding/delegation work because its
headless auto-approval path does not provide Gemini ACP-equivalent per-tool
read-only enforcement.

## 12. Backend and model routing

Settings exposes:

- **Default worker** — Gemini CLI by default;
- backend health;
- Gemini model override;
- OpenCode provider/model;
- model-profile routes for `strongest`, `coding`, and `research`.

For example, you may keep Gemini as the default/strongest worker while routing
`coding` to OpenCode temporarily.

Routes are persisted and concrete backend/model selection is recorded on each
Execution so later settings changes do not rewrite execution provenance.

## 13. Legacy Gemini Advanced sessions

WP11 `sceneworks.agent_session.*` tools remain for compatibility. They represent
a persistent Gemini ACP provider conversation and are now labelled **legacy
Gemini ACP provider sessions**.

Use them only when you specifically want Gemini's persistent ACP conversation.
For general ChatGPT-controlled engineering, prefer `EngineeringSession` plus
direct runtime tools.

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

### New WP14 tools do not appear

The plugin tool schema comes from the running SceneWorks instance. Run the WP14
code, reconnect/refresh the tunnel/plugin, and rescan tools after changing MCP
mode.

### I can read but cannot register PCS

Project registration requires Standard or Advanced mode.

### I can register PCS but cannot create a worktree

EngineeringSession creation requires Advanced mode and a writable configured
`worktree_root` accessible to the SceneWorks host.

### A direct command is rejected

The EngineeringSession lacks `shell_execute`, or the command/cwd is invalid.
Persistent processes require `process_control` separately.

### OpenCode is unavailable

Install/configure the OpenCode executable and its own provider credentials, then
refresh backend health. SceneWorks does not store those provider secrets.

See [WP14 provider-neutral execution](../wp14-provider-neutral-execution.md) for
the architecture/security contract.
