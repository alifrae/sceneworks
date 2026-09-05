# WP21 Lifecycle Supervisor

WP21 makes SceneWorks infrastructure lifecycle **semantic, local, bounded, and
out-of-process**. The supervisor manages only three components:

- `api`
- `web`
- `mcp_tunnel`

It is a standard-library Python service bound only to `127.0.0.1:8020`. It is
not part of FastAPI and therefore survives API restarts.

## Ownership model

The supervisor is the lifecycle authority for SceneWorks infrastructure.
PowerShell, FastAPI, the web UI, MCP clients, and model/provider agents do not
receive raw process authority.

A process may be stopped only when SceneWorks can prove ownership using its
persisted managed-process metadata and expected command fingerprint. During the
WP21 migration an already-running service may be adopted only when both its
fixed listener port and SceneWorks command fingerprint match. A port match alone
never authorizes termination.

If ownership is ambiguous, the component becomes `UNKNOWN`/`DEGRADED` and the
supervisor fails closed.

## State and recovery

Component states are:

`STOPPED`, `STARTING`, `HEALTHY`, `UNHEALTHY`, `RECOVERING`, `DEGRADED`, and
`UNKNOWN`.

Health monitoring runs every five seconds. A running component needs three
consecutive failed health samples before automatic recovery. An owned process
that exits is eligible for immediate recovery.

Automatic recovery is bounded:

- maximum 3 attempts inside a rolling 5-minute window;
- retry delays: 1 s, 2 s, 5 s;
- after budget exhaustion the component becomes `DEGRADED`;
- 10 continuous healthy minutes clear the restart budget.

Default startup grace is 45 s for API, 60 s for web, and 20 s for the MCP
tunnel.

`restart-all` stops in `mcp_tunnel -> web -> api` order and starts in
`api -> web -> mcp_tunnel` order.

## Durable operations

Mutating lifecycle requests are recorded in the SQLite operation journal before
execution and return an `operation_id`. Rows contain bounded operational metadata
only: actor, semantic action/component, timestamps, state, and bounded/redacted
detail.

The journal never stores environment dictionaries, credentials, bearer tokens,
or arbitrary command strings.

## Control surfaces

The loopback supervisor API exposes bounded status/operation reads and semantic
start/stop/restart/reconcile actions. Mutations require the local bearer token.

FastAPI exposes only:

- `sceneworks.system.status`
- `sceneworks.system.restart` with `api|web|mcp_tunnel|all`

The MCP schema accepts no PID, port, path, URL, executable, command, environment,
or shell input.

The Diagnostics page uses Next.js server-only proxy routes. Browser JavaScript
never receives the supervisor bearer token or its file path.

## Local data

Windows default:

```text
%LOCALAPPDATA%\SceneWorks\supervisor\
```

Non-Windows fallback:

```text
~/.local/share/sceneworks/supervisor/
```

The directory contains `token`, `processes.json`, and `supervisor.db`.

## Security boundary

WP21 is local lifecycle supervision, not remote machine administration. It does
not expose a general shell service, arbitrary executable launch, arbitrary port
management, environment injection, or download-and-execute behavior.

Provider agents are never lifecycle authority. Agent/model output remains
inference; SceneWorks-captured process/health/journal observations are the
operational evidence.

## Verification

CI requires:

- supervisor unit tests on Ubuntu;
- supervisor tests and PowerShell launcher parsing on Windows;
- non-live backend tests including the SupervisorClient/MCP contract;
- frontend unit tests and production build;
- the existing provider-independent deterministic qualification gate.

GitHub-hosted runners do not substitute for real-laptop qualification. The
remaining host-level acceptance check after merge is deliberately small:

1. kill the managed API and verify automatic recovery;
2. kill the managed tunnel and verify automatic recovery;
3. trigger a component restart from Diagnostics;
4. trigger `Restart SceneWorks` and verify the UI reconnects;
5. force repeated failure and verify `DEGRADED` after the bounded budget;
6. occupy a managed port with an unrelated process and verify SceneWorks refuses
to terminate it.

Those checks validate Windows host/process behavior; they do not change the WP21
architecture or API contract.

## Boundary to WP22

WP21 is local-only. A future hub/edge or remote recovery system must be a separate
trust boundary and must not turn this loopback supervisor into a remotely
accessible shell/process service.
