# Connect SceneWorks to ChatGPT with the MCP plugin

SceneWorks exposes one MCP endpoint with three authority levels. Start in
**Observe**, validate the tunnel/tool catalogue, then raise it deliberately.

```text
Observe  -> semantic reads
Standard -> governed actions + project registration
Advanced -> direct EngineeringSession + evidence + semantic PCS control + optional workers
```

Gemini CLI remains the default agent worker, but Advanced direct execution,
PCS lifecycle control and evidence capture are owned by SceneWorks and do not
require a functioning Gemini model session.

## 1. Start SceneWorks and verify MCP locally

Default endpoint:

```text
http://127.0.0.1:8010/mcp
```

`GET /mcp` is discovery only. ChatGPT uses `POST /mcp` JSON-RPC.

## 2. Connect ChatGPT through Tunnel

For SceneWorks running on your laptop/private workstation:

1. Add a custom MCP plugin/integration named `SceneWorks`.
2. Choose the trusted **Tunnel** connection.
3. Target `http://127.0.0.1:8010/mcp`.
4. Review the tool catalogue before raising SceneWorks above Observe mode.

Do not expose port `8010` directly to the public internet. The bare SceneWorks
service has no user login/OAuth boundary.

## 3. Observe mode

Configure:

```env
SCENEWORKS_MCP_MODE=observe
```

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
sceneworks.pcs.get_config
```

No worktree/process/mutation tools are exposed. `pcs.get_config` returns only an
MCP-safe projection: configured profile/runbook semantics and asset aliases, not
external host roots or environment values.

## 4. Standard mode and programmatic project registration

Configure:

```env
SCENEWORKS_MCP_MODE=standard
```

Standard adds governed SceneWorks actions plus:

```text
sceneworks.register_project
```

If PCS is already cloned on the machine where SceneWorks runs:

```text
@SceneWorks register the Git project at C:\path\to\PCS as PCS.
```

The path is resolved on the **SceneWorks host**, not by ChatGPT. Registration
validates Git and does not upload/copy the repository through the tunnel.

## 5. Advanced mode

Configure:

```env
SCENEWORKS_MCP_MODE=advanced
```

Advanced permissions include:

```text
repository_read
repository_write
shell_execute
process_control
git_commit
agent_delegate
external_asset_read
```

`network_access` remains a host/provider capability indicator and `subagents`
exists for legacy Gemini ACP sessions.

`external_asset_read` is independent of repository/process control. Use it only
when PCS needs recordings/corpora outside the worktree.

## 6. Create a task-bound EngineeringSession

For assigned work, bind the session to the task:

```text
sceneworks.engineering_session.create(
  project_id=...,
  task_id=142,
  permissions=[...]
)
```

SceneWorks creates a local isolated worktree/branch such as:

```text
sw/mcp-<session-id>
```

The absolute worktree path is never exposed through MCP.

## 7. Use explicit supervisor turns

WP15 groups iterative work into causal turns:

```text
sceneworks.engineering_session.begin_turn
sceneworks.engineering_session.finish_turn
```

Typical sequence:

```text
turn 1: reproduce
turn 2: investigate
turn 3: implement
turn 4: verify
```

Reuse the returned `turn_id` on direct actions/delegations triggered by that
iteration. One session may have only one active turn.

## 8. Direct engineering primitives

Without Gemini, ChatGPT can use:

```text
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
```

These remain useful generic primitives. For PCS lifecycle/observation, prefer the
semantic `pcs.*` tools below because they add profiles, persistent logs, health,
crash semantics and PCS-specific evidence automatically.

## 9. Configure PCS run profiles

PCS configuration is project-scoped and persistent. The trusted local REST
configuration surface is:

```text
GET /api/projects/<project-id>/pcs-control
PUT /api/projects/<project-id>/pcs-control
```

ChatGPT in Advanced can also call:

```text
sceneworks.pcs.configure
```

Example profile:

```json
{
  "default_profile": "debug",
  "profiles": {
    "debug": {
      "command": "uv",
      "args": ["run", "python", "-m", "app.main"],
      "cwd": "backend",
      "expected_ports": [
        {"name": "backend", "host": "127.0.0.1", "port": 8010}
      ],
      "log_paths": ["logs/pcs.log"],
      "crash_paths": ["crash/pcs.dmp"],
      "api_base_url": "http://127.0.0.1:8010",
      "health_path": "/api/health",
      "runtime_state_path": "/api/runtime/state"
    }
  }
}
```

Important constraints:

- cwd/log/crash paths are worktree-relative;
- health/API probes are loopback-only;
- persisted profile environments may not contain secret-looking keys;
- `pcs.get_config` hides environment values and external asset roots from MCP.

## 10. Start/stop/restart PCS semantically

Use:

```text
sceneworks.pcs.start
sceneworks.pcs.status
sceneworks.pcs.stop
sceneworks.pcs.restart
```

Example:

```text
@SceneWorks start PCS in this engineering session using the debug profile and correlate it with the current turn.
```

The path is:

```text
ChatGPT
  -> SceneWorks MCP
     -> PCS control service
        -> EngineeringSession ExecutionRuntime
           -> PCS process
```

Gemini is not in that chain.

`pcs.start` returns the managed run id, OS PID, selected profile and optional
startup health result. SceneWorks then keeps monitoring the process independently
of individual agent/provider turns.

A session cannot be closed while it owns a live managed PCS run. Call
`sceneworks.pcs.stop` first.

## 11. Structured PCS logs

SceneWorks continuously drains PCS stdout/stderr into durable WP15 evidence.
Use:

```text
sceneworks.pcs.logs
sceneworks.pcs.errors
sceneworks.pcs.log_tail
```

Events include:

```text
timestamp
severity
source (stdout/stderr)
text
run correlation
```

Examples:

```text
@SceneWorks show only ERROR/CRITICAL PCS logs from this run.
```

```text
@SceneWorks tail the last 50 PCS log events containing "renderer".
```

The log evidence is bounded and intended for engineering verification, not as a
replacement for a production log platform.

## 12. Crash and exit evidence

SceneWorks distinguishes:

```text
explicit stop          -> STOPPED
self-exit code 0       -> EXITED
self-exit non-zero     -> CRASHED
lost native handle     -> LOST
```

Before finalization, remaining bounded stdout/stderr is persisted. Configured
worktree-relative log/crash files are recorded with path, size, timestamp and
SHA-256 (up to the documented hash bound).

A Gemini statement such as "PCS crashed because X" is still inference. The
exit code, captured fatal logs and crash/log-file hashes are evidence.

## 13. PCS health checks

Use:

```text
sceneworks.pcs.health
```

It combines:

- managed process state;
- configured loopback TCP ports;
- optional loopback PCS API health response.

If no checks are configured, a live managed process is considered ready.

Remote hosts are intentionally rejected in WP16 so semantic health cannot become
an arbitrary network scanner.

## 14. Semantic PCS runtime state

Use:

```text
sceneworks.pcs.runtime_state
```

When a profile defines a PCS `runtime_state_path`, SceneWorks reads that PCS API
and treats it as the semantic source.

Without such an API, SceneWorks reports objective process state and explicit
unknowns for:

```text
active recording
frame
playback state
loaded configuration
active views
errors/warnings
```

It does not infer those fields from logs or ask Gemini to guess.

As PCS API hardening exposes more capabilities, prefer adding them here rather
than automating equivalent GUI interactions.

## 15. External recordings and test assets

Configure explicit read-only aliases, for example:

```json
{
  "asset_roots": {
    "regression": {
      "path": "D:\\PCS\\RegressionCorpus",
      "read_only": true
    }
  }
}
```

The EngineeringSession must grant:

```text
external_asset_read
```

Then ChatGPT can use:

```text
sceneworks.pcs.assets
sceneworks.pcs.asset_info
```

Only alias-relative paths/metadata are returned. The configured absolute root is
not exposed through MCP.

To pass an asset to PCS/runbook command arguments:

```text
{{asset:regression:playback/frame32.dat}}
```

SceneWorks resolves that placeholder locally after permission/confinement checks.
The resolved absolute path is not persisted/echoed into MCP evidence.

Asset access is read-only in WP16.

## 16. Deterministic PCS verification runbooks

Runbooks encode repeatable verification procedures rather than prompts.
Supported steps:

```text
command
start
stop
restart
health
runtime_state
```

Example:

```json
{
  "runbooks": {
    "startup-smoke": {
      "stop_on_failure": true,
      "steps": [
        {"action": "start", "profile": "debug"},
        {"action": "health"},
        {"action": "runtime_state"},
        {"action": "stop"}
      ]
    }
  }
}
```

Run it with:

```text
sceneworks.pcs.run_verification
```

Each step produces objective evidence and the runbook returns pass/fail. This is
preferred to repeatedly telling a worker how to launch/build/smoke-test PCS.

## 17. Inspect evidence independently

Use:

```text
sceneworks.engineering_session.evidence
sceneworks.engineering_session.events
sceneworks.engineering_session.summary
```

The evidence stream now includes PCS lifecycle, structured log, health/runtime
observations and runbook results in addition to WP15 file/command/process/Git
information.

For task closure, ask for the task contract + current Git diff + EngineeringSession
evidence instead of asking Gemini whether its fix worked.

## 18. Delegate implementation when useful

Optional worker delegation remains:

```text
sceneworks.agent.delegate
```

Typical backends:

```text
backend=gemini_acp   # default
backend=opencode     # non-ACP backup
backend=openhands    # experimental
```

A good PCS bug loop is now:

```text
ChatGPT creates/binds session
  -> starts PCS semantically
  -> reproduces / reads objective logs + state
  -> delegates code investigation/implementation if useful
  -> inspects actual Git diff
  -> restarts PCS semantically
  -> runs deterministic verification runbook
  -> compares evidence with acceptance criteria
```

If Gemini authentication is broken, the direct PCS/runtime/evidence path still
works.

## 19. Project/session cleanup

- Stop managed PCS before closing an EngineeringSession.
- Active EngineeringSessions/PCS runs block project deletion even with legacy
  `force=true` cleanup.
- `purge_history=true` removes SceneWorks PCS/session/evidence/config records but
  never deletes the Git repository or configured external recordings.
- If SceneWorks itself restarts while PCS was active, the old managed run is
  marked `LOST`; native process handles cannot currently be recovered.

## Security boundary

Before enabling Advanced:

- keep SceneWorks local/private or behind authenticated TLS;
- grant only required EngineeringSession permissions;
- treat configured launch commands as trusted operator configuration;
- understand command/process execution has the SceneWorks OS user's authority;
- understand external asset aliases govern SceneWorks access but do not create an
  OS sandbox around a child process that receives an asset path;
- keep PCS API/health probes loopback-only;
- review Git diff and objective evidence before integration.

## Troubleshooting

### ChatGPT cannot reach SceneWorks

Confirm SceneWorks is running, `GET http://127.0.0.1:8010/mcp` works locally and
the ChatGPT tunnel is connected.

### New WP16 tools do not appear

Run the WP16 code, reconnect/refresh the tunnel/plugin, and rescan tools after
changing MCP mode. The schema comes from the currently running SceneWorks
backend.

### PCS cannot start

Check:

- Advanced mode is enabled;
- the EngineeringSession grants `process_control`;
- the run profile exists and its cwd is valid in the session worktree;
- any referenced `{{asset:...}}` exists and `external_asset_read` is granted.

### PCS starts but health never becomes ready

Inspect `pcs.logs`, `pcs.status` and `pcs.health`. Confirm the configured local
ports/API endpoint actually belong to the selected profile.

### I can see an asset alias but cannot read it

The session lacks `external_asset_read`, the relative path escapes the root, or
the configured host directory no longer exists.

### Runtime state shows null/unknown fields

Configure a hardened PCS runtime-state API endpoint. SceneWorks deliberately does
not fabricate playback/frame/view state from logs.

See [WP14 provider-neutral execution](../wp14-provider-neutral-execution.md),
[WP15 evidence ledger](../wp15-evidence-ledger.md), and
[WP16 PCS runtime control](../wp16-pcs-runtime-control.md).
