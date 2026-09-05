# WP21 Lifecycle Supervisor Design

## Purpose

WP21 adds an out-of-process lifecycle supervisor for the local SceneWorks stack. The supervisor must remain available when the FastAPI backend is unhealthy or intentionally restarting, recover deterministic service failures automatically, and provide a narrow governed control surface for the web UI and future remote control-plane work.

The immediate target is the current Windows deployment. The design must not block a later provider-neutral hub/edge architecture, but WP21 does not introduce distributed scheduling or a cloud control plane.

## Problem

Today `scripts/start-sceneworks.ps1` owns startup and `-Restart` can stop and relaunch the API, frontend, and Secure MCP tunnel. This is useful but operator-driven. If the MCP tunnel dies, ChatGPT loses the SceneWorks control path and cannot use SceneWorks itself to repair that tunnel. If the API dies, the current web UI can report failure but cannot recover it through the failed API.

Lifecycle management therefore needs a process boundary outside the services being managed.

## Scope

WP21 manages exactly these SceneWorks components:

- `api` — FastAPI backend on loopback port 8010;
- `web` — production Next.js frontend on loopback port 3000;
- `mcp_tunnel` — Secure MCP tunnel readiness endpoint on loopback port 8080.

WP21 provides:

- component status and aggregate stack status;
- start, stop, restart, and restart-all operations;
- continuous health monitoring;
- bounded automatic recovery;
- crash-loop detection and explicit `DEGRADED` state;
- durable lifecycle operation/audit records;
- a loopback-only supervisor control API that survives FastAPI restart;
- Diagnostics UI status and lifecycle controls;
- semantic MCP lifecycle status/restart tools;
- launcher integration so the existing Windows entry point delegates lifecycle ownership rather than duplicating it;
- deterministic tests with fake process/health providers;
- host-level Windows qualification for real process restart and tunnel recovery.

## Non-goals

WP21 does not add:

- a cloud SceneWorks Hub;
- distributed workers or scheduling;
- arbitrary remote shell execution;
- arbitrary URL download-and-execute behavior;
- OAuth, end-user RBAC, or multi-user IAM;
- OS/container sandboxing;
- automatic merge or autonomous Git integration changes;
- generic operating-system service management outside the three SceneWorks components.

## Architecture

### Process boundary

The supervisor is a separate long-lived local process:

```text
Browser
  |
  | same-origin request
  v
Next.js server route
  |
  | authenticated loopback supervisor request
  v
SceneWorks Lifecycle Supervisor
  |-- FastAPI backend
  |-- Next.js frontend
  `-- Secure MCP tunnel
```

The supervisor must not be hosted inside FastAPI. A backend restart must never terminate the lifecycle authority responsible for that restart.

### Implementation shape

Add a focused Python package under `supervisor/` using the Python standard library wherever practical. It owns:

- component definitions;
- health probes;
- process launch/termination abstraction;
- reconciliation;
- restart policy;
- durable operation journal;
- loopback control server.

Platform-specific process discovery/termination stays behind a provider boundary. CI uses deterministic fakes. The Windows provider may use fixed internal PowerShell/CIM/taskkill calls where necessary, but caller-supplied shell text must not cross that boundary.

The initial Windows startup integration remains script-driven. `scripts/start-sceneworks.ps1` becomes bootstrap/install/start glue for the supervisor instead of independently owning normal service restart semantics.

## Component state model

Each component exposes one of:

- `STOPPED` — intentionally not running and health endpoint unavailable;
- `STARTING` — launch accepted, still within startup grace period;
- `HEALTHY` — process ownership and health probe agree;
- `UNHEALTHY` — process exists or endpoint is reachable but health requirements are not satisfied;
- `RECOVERING` — supervisor is performing bounded automatic recovery;
- `DEGRADED` — restart budget exhausted or ownership cannot be established safely;
- `UNKNOWN` — state cannot be determined without guessing.

Aggregate stack state is:

- `HEALTHY` only when all configured components are `HEALTHY`;
- `DEGRADED` if any component is `DEGRADED`;
- otherwise `UNHEALTHY` when at least one required component is not healthy.

The supervisor must not fabricate `STOPPED` merely because a probe fails.

## Component ownership

The supervisor must not kill arbitrary listeners solely because they use ports 8010, 3000, or 8080.

A component is controllable only when ownership can be established from supervisor-started process metadata or a validated SceneWorks process fingerprint. The existing launcher rule that avoids blindly terminating unrelated port owners remains an invariant.

Persisted process metadata includes at minimum:

- component key;
- supervisor generation;
- root PID and start timestamp when known;
- launch command identity/fingerprint, not secret-bearing environment values;
- last known health state;
- last transition timestamp.

After supervisor restart, reconciliation re-establishes ownership where safely possible. Ambiguous ownership produces `DEGRADED`/`UNKNOWN`, not forced termination.

## Launch contracts

The supervisor owns the normal launch commands already used by the Windows launcher:

- API: from `backend/`, `uv run python -m app.main`;
- web: from `web/`, `npm run start`;
- MCP tunnel: configured trusted tunnel executable with `run`, targeting the existing SceneWorks MCP URL.

Dependency synchronization and frontend production build remain bootstrap/provisioning concerns. Normal lifecycle restart must not run `uv sync` or `npm run build` unless the launcher is explicitly invoked for provisioning/rebuild.

The supervisor never persists provider credentials, tunnel API keys, or secret-bearing environment values. Child processes inherit required secrets from the supervisor process environment or another future secret provider.

## Health contracts

Default probes retain current loopback semantics:

- API: `http://127.0.0.1:8010/api/health`;
- web: `http://127.0.0.1:3000`;
- MCP tunnel: `http://127.0.0.1:8080/readyz`.

Monitoring cadence: every 5 seconds.

Startup grace periods:

- API: 45 seconds;
- web: 60 seconds;
- MCP tunnel: 20 seconds.

A single failed health sample does not immediately restart a previously healthy component. Automatic recovery begins after 3 consecutive failed monitor samples, except when the owned process is observed to have exited, which can trigger recovery immediately.

## Automatic recovery policy

Automatic recovery is enabled by default for `api`, `web`, and `mcp_tunnel`.

For each component:

- allow at most 3 automatic restart attempts in a rolling 5-minute window;
- use retry delays of 1, 2, and 5 seconds before attempts 1, 2, and 3;
- after the third failed attempt, set the component to `DEGRADED` and stop automatic restarts;
- a successful continuous healthy period of 10 minutes clears the automatic restart budget;
- an explicit operator restart is allowed while `DEGRADED` and starts a fresh manual operation, but does not erase the audit history.

The supervisor must never spin in an unbounded restart loop.

## Lifecycle operations

Control API operations are asynchronous jobs. A mutating request returns an `operation_id` after durable acceptance; the supervisor then performs the requested mutation. This is required so `restart_all` can acknowledge the UI before the web process is stopped.

Supported operations:

```text
status()
start(component)
stop(component)
restart(component)
restart_all()
reconcile()
operations(limit)
operation(operation_id)
```

`restart_all()` uses this order:

1. stop MCP tunnel;
2. stop web;
3. stop API;
4. start API and wait for health;
5. start web and wait for health;
6. start MCP tunnel and wait for readiness;
7. report aggregate result.

If an earlier start step fails, later dependent starts are not reported as healthy. The final operation result records partial progress explicitly.

## Durable operational journal

The supervisor owns a separate SQLite database under the current user's local application-data directory, outside Git worktrees and outside the main SceneWorks database.

Default Windows location:

```text
%LOCALAPPDATA%\SceneWorks\supervisor\supervisor.db
```

The journal stores bounded operational metadata:

- operation id;
- actor: `auto`, `user_ui`, `launcher`, `local_cli`, or `mcp`;
- requested action and component;
- accepted/start/end timestamps;
- state transitions;
- attempt number;
- exit code when available;
- bounded diagnostic code/detail;
- final result: `SUCCEEDED`, `FAILED`, `PARTIAL`, or `REJECTED`.

It must not store tunnel credentials, provider credentials, arbitrary command lines containing secrets, raw environment dumps, or unbounded stdout/stderr.

Lifecycle journal records are operational evidence of what the supervisor observed and did. Agent/model prose remains inference.

## Supervisor control API

The supervisor binds only to loopback. Default address:

```text
127.0.0.1:8020
```

The API is intentionally narrow and JSON-only.

Read endpoints expose status and bounded operation history. Mutations require a bearer token generated on first supervisor initialization and stored in the supervisor local-data directory with user-only permissions where the platform supports enforcement.

The browser never receives this token.

The Next.js server owns same-origin routes that proxy lifecycle requests to the supervisor. Server-side code reads the local supervisor credential; client-side bundles do not contain it.

This preserves recovery when FastAPI is down while avoiding a privileged secret in browser JavaScript.

## Web UI

Extend the existing Diagnostics page rather than adding another top-level administration area.

Add a `SceneWorks services` panel showing:

| Component | State | Last transition | Recovery | Action |
| --- | --- | --- | --- | --- |
| API | HEALTHY | timestamp | automatic | Restart |
| Web | HEALTHY | timestamp | automatic | Restart |
| MCP tunnel | DEGRADED | timestamp | 3/3 exhausted | Restart |

Also show aggregate stack state and the latest bounded lifecycle operation result.

Controls:

- `Restart` per component;
- `Restart SceneWorks` for the stack;
- `Run checks` continues to refresh browser/API/backend diagnostics;
- lifecycle mutations require an explicit confirmation in the UI;
- while an operation is active, the relevant control is disabled and the UI polls operation status;
- after `restart_all`, the page tolerates temporary disconnects and resumes polling when the frontend returns.

The main Control page remains observational for engineering sessions/PCS. WP21 infrastructure lifecycle controls belong in Diagnostics.

## Launcher integration

`scripts/start-sceneworks.ps1` remains the human bootstrap entry point.

Normal behavior becomes:

1. verify bootstrap prerequisites;
2. perform dependency sync/build only when needed under the existing rules;
3. ensure the lifecycle supervisor is installed/running;
4. ask the supervisor to reconcile/start configured components;
5. wait for aggregate readiness;
6. open the browser unless `-NoBrowser` is set.

`-Restart` delegates to supervisor `restart_all` instead of independently killing the stack.

`-NoTunnel` is represented as launch configuration for that bootstrap invocation; it does not silently delete persisted credentials or tunnel configuration.

## Runtime/tunnel provisioning safety

WP21 does not allow `download(url); execute()` from MCP, the UI, an agent, or a model-generated URL.

The supervisor may use an already configured tunnel executable path. It may also support installation from a locally supplied artifact only when the caller supplies an expected SHA-256 and the operation is initiated from the local bootstrap path.

Unattended remote download is deliberately out of scope until SceneWorks has an authoritative tunnel-runtime distribution source whose URL and integrity metadata can be pinned. Absence of that source is surfaced as `runtime_not_provisioned`, not worked around with arbitrary web download.

## MCP/autonomy boundary

WP21 exposes semantic lifecycle status/restart tools through the existing SceneWorks MCP server when the backend and tunnel are healthy. These tools delegate to the supervisor and never execute arbitrary shell commands.

MCP alone is not the recovery path for an MCP-tunnel outage. Automatic supervisor recovery is the recovery path. WP22 hub/edge can later provide an independent remote path.

Required semantic tools:

```text
sceneworks.system.status
sceneworks.system.restart
```

`sceneworks.system.restart` accepts only `api`, `web`, `mcp_tunnel`, or `all` and records actor/correlation metadata. No executable path, command string, PID, port, or URL is accepted from MCP.

## Failure handling

The supervisor must fail closed when lifecycle ownership is ambiguous.

Examples:

- health endpoint reachable but process ownership cannot be established -> `UNKNOWN`/`DEGRADED`, no kill;
- expected executable missing -> `FAILED` with `runtime_not_provisioned`;
- startup timeout -> failed attempt, eligible for bounded recovery;
- stop timeout -> `PARTIAL`/`FAILED`, preserve observed process metadata;
- supervisor database unavailable -> mutating operations rejected rather than performed without auditability;
- supervisor API credential missing/corrupt -> mutations rejected; local bootstrap can rotate/reinitialize through an explicit operator path;
- API down while web is up -> Diagnostics remains able to query supervisor through the Next.js server route and restart API;
- MCP tunnel down -> supervisor auto-recovers without depending on MCP.

## Security invariants

1. The lifecycle supervisor is not a general shell/runtime service.
2. Mutations are restricted to the three declared SceneWorks components.
3. The supervisor binds to loopback only in WP21.
4. Browser JavaScript never receives the supervisor bearer token.
5. Arbitrary PIDs, ports, executable paths, URLs, environment dictionaries, or command strings are not accepted by remote/UI/MCP lifecycle calls.
6. A port match alone is never sufficient authority to terminate a process.
7. Secret-bearing environment data is neither persisted nor returned in diagnostics.
8. Lifecycle actions and automatic recovery are durably attributable by operation id and actor.
9. Automatic restart is bounded and crash-loop safe.
10. Lifecycle implementation remains model/provider independent.

## Testing strategy

### Deterministic unit tests

Use fake process and health providers to cover:

- state transitions;
- 3-consecutive-failure threshold;
- immediate recovery after owned-process exit;
- 3-in-5-minute restart budget;
- retry delays;
- 10-minute healthy reset;
- safe refusal on ambiguous ownership;
- restart-all order;
- partial failure propagation;
- durable operation journal;
- credential-required mutations;
- no secret values in serialized status/history.

### Backend/MCP integration tests

Cover:

- semantic supervisor client behavior;
- MCP lifecycle status serialization/redaction;
- allowed component enum only;
- restart delegation with actor/correlation metadata;
- no raw command/path/PID/URL lifecycle inputs.

### Frontend tests

Cover:

- supervisor status rendering;
- degraded component rendering;
- confirmation before restart;
- operation polling;
- temporary disconnect during restart-all;
- controls disabled while an operation is active.

### Windows host qualification

On a real Windows host:

1. start the stack through the launcher;
2. verify all three components healthy;
3. terminate the tunnel process and verify automatic recovery without operator action;
4. terminate the API process and verify automatic recovery;
5. request API restart from Diagnostics while API is healthy;
6. request stack restart and verify UI reconnect;
7. induce repeated startup failure and verify `DEGRADED` after the bounded budget;
8. place an unrelated listener on a managed port and verify SceneWorks refuses destructive takeover when ownership is ambiguous.

## Acceptance criteria

WP21 is complete only when all of the following are true:

1. lifecycle authority runs outside FastAPI and survives backend restart;
2. API, web, and MCP tunnel have explicit semantic state and bounded health monitoring;
3. tunnel and API process loss recover automatically under the defined restart budget;
4. crash loops end in `DEGRADED` rather than unbounded restart;
5. restart operations are durably journaled before mutation;
6. Diagnostics can restart the API even when the API itself is unavailable, provided the frontend and supervisor are healthy;
7. `restart_all` is asynchronous and the UI reconnects after the frontend restart;
8. the launcher delegates normal lifecycle ownership to the supervisor;
9. `sceneworks.system.status` and `sceneworks.system.restart` are exposed through MCP and delegate only to semantic supervisor operations;
10. no lifecycle interface accepts arbitrary shell, PID, port, URL, executable path, or environment payloads from UI/MCP;
11. ambiguous process ownership never results in blind termination;
12. deterministic CI tests pass without requiring live PCS, a paid model provider, or the real tunnel runtime;
13. real Windows qualification covers process restart, tunnel recovery, crash-loop behavior, and unrelated-port safety;
14. canonical architecture, limitations, launcher, MCP, and web UI documentation are updated with the implemented behavior;
15. existing SceneWorks workflow, evidence, PCS, GUI, and provider-neutral invariants remain green.

## Forward compatibility

WP21 deliberately creates a `SupervisorClient` boundary so WP22 can later connect a cloud SceneWorks Hub to an edge supervisor without moving lifecycle semantics into provider agents or exposing raw host execution.

The future hub may add host identity, heartbeat, signed remote commands, and multi-host routing. Those capabilities are not required for WP21 and must not leak into its local lifecycle contract prematurely.
