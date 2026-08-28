# WP16 — PCS Runtime Control

## Goal

Make Point Cloud Studio a first-class, semantically controlled runtime under a
SceneWorks `EngineeringSession` so ChatGPT can reproduce, observe and verify PCS
behavior without delegating basic machine operations to Gemini or another agent.

WP16 builds on:

- WP14 provider-neutral direct execution;
- WP15 task/turn/action evidence correlation.

The authority chain is:

```text
Task (optional)
  -> EngineeringSession
     -> EngineeringTurn
        -> semantic pcs.* action
           -> SceneWorks ExecutionRuntime
              -> PCS process/API/assets
           -> SceneWorks evidence
```

Agent backends remain optional workers. They are not the PCS lifecycle
substrate.

## Scope

WP16 adds:

- persistent project-scoped PCS runtime configuration;
- named launch/run profiles;
- SceneWorks-managed PCS process lifecycle;
- continuous structured stdout/stderr evidence;
- non-zero exit/crash evidence;
- configured log/crash-file metadata and hashes;
- loopback health checks;
- semantic PCS runtime-state reads through a configured PCS API;
- explicit read-only external recording/asset aliases;
- deterministic verification runbooks;
- semantic MCP tools for all of the above;
- a dedicated `external_asset_read` Advanced-session permission.

WP16 does **not** add GUI screenshots or GUI automation. Those belong to later
visual-control work and should remain secondary to PCS APIs.

## Persistent model

PCS-specific configuration deliberately does not add fields to the generic
`Project` table.

### `pcs_project_control`

One row per project containing a validated `PcsRuntimeControlConfig`.

### `pcs_runs`

One row per SceneWorks-managed PCS process. It records:

- project and EngineeringSession;
- optional Task and EngineeringTurn;
- start `action_id`;
- selected profile;
- internal native `process_id`;
- OS PID;
- status and exit code;
- durable output cursor;
- start/finish timestamps.

The internal native process id and absolute worktree path are not exposed as PCS
MCP authority.

## Run profiles

A run profile defines how PCS is launched, not how a model reasons about it.

Example configuration:

```json
{
  "default_profile": "debug",
  "profiles": {
    "debug": {
      "command": "uv",
      "args": ["run", "python", "-m", "app.main"],
      "cwd": "backend",
      "environment": {
        "PCS_LOG_LEVEL": "DEBUG"
      },
      "expected_ports": [
        {"name": "backend", "host": "127.0.0.1", "port": 8010}
      ],
      "log_paths": ["logs/pcs.log"],
      "crash_paths": ["crash/pcs.dmp"],
      "api_base_url": "http://127.0.0.1:8010",
      "health_path": "/api/health",
      "runtime_state_path": "/api/runtime/state",
      "startup_timeout_seconds": 30
    }
  }
}
```

`cwd`, `log_paths`, and `crash_paths` are worktree-relative. Health targets are
loopback-only in WP16.

### Secrets

PCS runtime configuration is persisted in SceneWorks and is therefore **not a
secret store**. Environment keys that look credential-bearing (`TOKEN`,
`SECRET`, `PASSWORD`, `API_KEY`, etc.) are rejected. Provider and application
secrets belong in their normal secure environment/configuration mechanisms.

## Managed process lifecycle

Semantic operations:

```text
pcs.start
pcs.status
pcs.stop
pcs.restart
```

`pcs.start`:

1. validates the EngineeringSession and `process_control` permission;
2. resolves the selected project profile;
3. resolves any governed external asset placeholders;
4. starts the process through the session's `ExecutionRuntime`;
5. persists a `PcsRun`;
6. records start evidence;
7. starts continuous output capture;
8. optionally waits for configured health checks.

A managed PCS process survives individual ChatGPT/Gemini turns because ownership
is SceneWorks runtime state, not provider conversation state.

The native runtime's process handle does not survive a SceneWorks backend
restart. On startup, a previously active `PcsRun` is marked `LOST` and that fact
is added to the evidence ledger rather than pretending the process was cleanly
stopped.

## Structured logs

SceneWorks drains PCS stdout/stderr continuously. Durable log events contain:

```text
sequence
timestamp
severity
source = stdout | stderr
text
run_id
PID
```

Severity uses explicit common level tokens when present and otherwise defaults
to `error` for stderr and `info` for stdout.

MCP provides:

```text
sceneworks.pcs.logs
sceneworks.pcs.errors
sceneworks.pcs.log_tail
```

Filters include run, severity, source and text containment. Retrieval operates
on WP15 durable evidence, not on a Gemini-generated summary.

Log evidence is bounded. SceneWorks is not intended to replace a production log
store.

## Crash/exit evidence

A managed process that exits itself with code 0 becomes `EXITED`; a non-zero exit
becomes `CRASHED`. An explicit SceneWorks stop becomes `STOPPED`.

Before finalizing a run, SceneWorks drains the remaining bounded stdout/stderr.
It also inspects configured worktree-relative `log_paths` and `crash_paths` and
records:

- relative path;
- existence;
- size;
- modification timestamp;
- SHA-256 for files up to 64 MiB.

WP16 stores artifact **evidence metadata**, not arbitrary crash-dump binary
content in SQLite. Binary artifact archival can be added later if a dedicated
artifact retention design is required.

## Health checks

`sceneworks.pcs.health` combines:

- managed process state;
- configured loopback TCP-port checks;
- an optional loopback PCS API health endpoint.

No checks means a live managed process is considered ready. Configured checks
must all pass for `ready=true`.

WP16 deliberately restricts network probes to loopback so PCS health semantics
do not become an arbitrary network scanner.

## Semantic runtime state

`sceneworks.pcs.runtime_state` prefers PCS's own hardened API.

When a profile has `api_base_url` + `runtime_state_path`, SceneWorks returns the
PCS API result as the semantic source.

When no runtime-state API exists, SceneWorks returns objective process state plus
explicit unknowns for fields such as:

- active recording;
- frame;
- playback state;
- loaded configuration;
- active views;
- PCS warnings/errors.

It does not infer them from logs or ask an agent to guess them.

This preserves the standing rule: **prefer PCS API control/observation over GUI
automation whenever PCS API hardening can expose the same state deterministically.**

## External recordings/assets

Large recordings often live outside Git. WP16 does not widen MCP into arbitrary
filesystem access.

A project can define explicit read-only aliases:

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

Access additionally requires the EngineeringSession permission:

```text
external_asset_read
```

MCP exposes only the alias and relative entries:

```text
sceneworks.pcs.assets
sceneworks.pcs.asset_info
```

Absolute configured asset-root paths are not returned through MCP.

Profiles/runbooks may pass a recording to PCS using a server-side placeholder:

```text
{{asset:regression:playback/frame32.dat}}
```

SceneWorks resolves it locally for the child process after confinement and
permission checks. Evidence preserves the alias/relative metadata, not the
resolved absolute host path.

WP16 asset access is read-only.

## Verification runbooks

A runbook is a deterministic procedure, not a model prompt.

Supported WP16 steps:

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
      "description": "Start PCS and verify health",
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

`command` steps execute through `ExecutionRuntime.run_command` and require
`shell_execute`. Start/stop/restart require `process_control`. External asset
placeholders require `external_asset_read`.

Every step is correlated with the EngineeringSession/Task/Turn/action and the
runbook produces an objective final pass/fail evidence record.

## MCP surface

### Configuration

```text
sceneworks.pcs.get_config
sceneworks.pcs.configure
```

`get_config` is safe to expose in Observe mode and hides asset roots/environment
values. Configuration mutation requires Advanced mode.

### Runtime

```text
sceneworks.pcs.start
sceneworks.pcs.stop
sceneworks.pcs.restart
sceneworks.pcs.status
sceneworks.pcs.health
sceneworks.pcs.runtime_state
```

### Evidence/logs

```text
sceneworks.pcs.logs
sceneworks.pcs.errors
sceneworks.pcs.log_tail
```

### Assets

```text
sceneworks.pcs.assets
sceneworks.pcs.asset_info
```

### Deterministic verification

```text
sceneworks.pcs.run_verification
```

## Project configuration API

Trusted local SceneWorks UI/API clients can manage the full configuration:

```text
GET /api/projects/{project_id}/pcs-control
PUT /api/projects/{project_id}/pcs-control
```

The local REST API returns the configured host asset paths because it is the
operator configuration surface. The MCP projection intentionally removes them.

## Permission model

WP16 uses existing permissions plus:

```text
external_asset_read
```

Typical PCS investigation session:

```text
repository_read
repository_write
shell_execute
process_control
external_asset_read
agent_delegate        # optional
```

`git_commit` remains separate.

## Session/project cleanup

A direct EngineeringSession cannot be closed while it owns an active managed PCS
run. Stop PCS first.

A project cannot be deleted while it has an active direct EngineeringSession or
PCS run, even with the legacy `force` cleanup flag. This prevents orphaned
process/worktree authority.

`purge_history=true` removes SceneWorks PCS configuration/run/evidence records in
foreign-key-safe order. It never deletes Git repository files or configured
external recordings.

## Security boundary

WP16 improves semantic governance but does not turn local process execution into
an OS sandbox.

- PCS child processes run with the SceneWorks OS user's authority.
- run-profile commands are operator configuration and therefore trusted at the
  same level as enabling Advanced command/process control.
- external asset paths are read-only at the SceneWorks API level, but a child
  process receiving an asset path still executes with host OS authority.
- loopback restrictions prevent the semantic health/runtime-state interface from
  becoming arbitrary remote network access.
- the bare SceneWorks API/MCP endpoint remains a trusted local/private control
  plane and should not be published unauthenticated.

## Acceptance criteria

WP16 is complete when deterministic tests demonstrate that:

1. project PCS configuration persists and MCP hides host asset roots/secrets;
2. PCS can be started/stopped/restarted without an agent provider;
3. PID/status/exit/log evidence is captured continuously;
4. non-zero exits become crash evidence;
5. final stdout/stderr is not discarded during stop/exit finalization;
6. runtime state prefers a configured PCS API and otherwise reports unknowns;
7. external assets require aliases + `external_asset_read` and reject traversal;
8. runbooks produce deterministic evidence and pass/fail results;
9. active PCS runs cannot be orphaned by session/project deletion;
10. migration/model schema, non-live backend suite, provider-independent
    qualification and frontend production build remain green.

## Deferred

The following are intentionally not WP16:

- GUI screenshots and dialog capture;
- Windows UI Automation / click/keyboard interaction;
- screenshot-before/after visual verification;
- binary minidump archival/retention service;
- process-tree/child-process inventory beyond the managed root PID;
- remote/non-loopback PCS health probes;
- OS/container-level command/network sandboxing.

Those require their own explicit authority and retention designs rather than
being silently folded into the PCS runtime adapter.
