# Windows launcher

SceneWorks' Windows startup entry points live under `scripts/`:

```powershell
.\scripts\start-sceneworks.ps1
```

or through the convenience wrapper:

```powershell
.\scripts\start-sceneworks.cmd
```

`start-sceneworks.ps1` is bootstrap/provisioning glue. WP21 moved infrastructure
lifecycle ownership out of PowerShell into the local **SceneWorks Supervisor**.
The supervisor runs separately from FastAPI on `127.0.0.1:8020`, so it remains
available while the API or web process is being restarted.

The launcher still:

- verifies `uv` and `npm`;
- installs frontend dependencies when required;
- builds the production frontend when stale or when `-Rebuild` is supplied;
- runs `uv sync --frozen --extra openhands` for the backend environment;
- starts/reuses the out-of-process supervisor;
- reads the supervisor token only from the local user data directory;
- submits semantic `reconcile`, `start`, or `restart-all` operations;
- opens the browser unless `-NoBrowser` is used.

The launcher **does not** terminate listeners by PID/port. Process-tree ownership,
fingerprint checks, health monitoring, recovery budgets, and stop/start ordering
belong to the supervisor. A matching port by itself is never authority to kill a
process.

## Normal startup

```powershell
.\scripts\start-sceneworks.ps1
```

The launcher starts or reuses the supervisor, submits `reconcile`, then requests
semantic starts for API and web plus the MCP tunnel when enabled. Existing
pre-WP21 services may be adopted only when both the expected fixed listener and
the expected SceneWorks command fingerprint match.

## Restart

```powershell
.\scripts\start-sceneworks.ps1 -Restart
```

`-Restart` maps to the supervisor's journaled `restart-all` operation. The
supervisor stops in dependency order `mcp_tunnel -> web -> api` and starts in
`api -> web -> mcp_tunnel` order. Operation acceptance is persisted before
process mutation.

## Other options

For UI development:

```powershell
.\scripts\start-sceneworks.ps1 -Dev
```

Force a production frontend rebuild:

```powershell
.\scripts\start-sceneworks.ps1 -Rebuild
```

Do not open the browser:

```powershell
.\scripts\start-sceneworks.ps1 -NoBrowser
```

Disable tunnel supervision when starting a new supervisor instance:

```powershell
.\scripts\start-sceneworks.ps1 -NoTunnel
```

A supervisor already running at `127.0.0.1:8020` retains the configuration with
which it was started. Restart the supervisor process itself before changing
`-Dev`/`-NoTunnel` bootstrap configuration.

## Secure MCP tunnel

The default executable location is:

```text
tools\tunnel-client-runtime-cloudflared.exe
```

Override it with `-TunnelClientPath` or `SCENEWORKS_TUNNEL_CLIENT_PATH`. Tunnel
startup requires `CONTROL_PLANE_TUNNEL_ID` and `CONTROL_PLANE_API_KEY`. The MCP
target defaults to `http://127.0.0.1:8010/mcp` and may be overridden by
`MCP_SERVER_URL` or `-McpServerUrl`.

If the executable or credentials are unavailable, the launcher disables tunnel
supervision for that newly started supervisor and continues with API/web.
Credentials and environment dictionaries are never persisted in supervisor
process metadata or lifecycle journal rows.

## Local supervisor state

On Windows the supervisor stores bounded local state under:

```text
%LOCALAPPDATA%\SceneWorks\supervisor\
```

This includes the bearer token, process ownership metadata, and SQLite operation
journal. The token is used by PowerShell, FastAPI's server-side client, and
Next.js server routes. It is never sent to browser JavaScript or exposed through
MCP responses.

See [WP21 lifecycle supervisor](../wp21-lifecycle-supervisor.md) for recovery,
security, and qualification details.
