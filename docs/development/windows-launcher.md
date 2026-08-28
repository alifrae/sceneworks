# Windows launcher

SceneWorks' Windows startup entry points live under `scripts/`:

```powershell
.\scripts\start-sceneworks.ps1
```

or double-click/run:

```powershell
.\scripts\start-sceneworks.cmd
```

The `.cmd` file is only a convenience wrapper. `start-sceneworks.ps1` is the
single startup orchestrator.

The launcher:

- verifies `uv` and `npm` are available;
- installs frontend dependencies when `node_modules` is missing;
- builds the Next.js production frontend when the existing build is missing or older than frontend source;
- starts/reuses the FastAPI backend and waits for `/api/health`;
- starts/reuses the frontend and waits for port 3000;
- optionally verifies `/mcp` and starts the Secure MCP tunnel in its own PowerShell window;
- waits for the tunnel readiness endpoint at `http://127.0.0.1:8080/readyz`;
- opens SceneWorks in the default browser unless `-NoBrowser` is used.

Normal use should stay in production mode because Next.js development mode can
compile a route the first time it is visited, which makes navigation look slower
than the API actually is.

## Options

For UI development:

```powershell
.\scripts\start-sceneworks.ps1 -Dev
```

Force a fresh frontend production build:

```powershell
.\scripts\start-sceneworks.ps1 -Rebuild
```

Start without opening the browser:

```powershell
.\scripts\start-sceneworks.ps1 -NoBrowser
```

Start SceneWorks without the ChatGPT Secure MCP tunnel:

```powershell
.\scripts\start-sceneworks.ps1 -NoTunnel
```

## Secure MCP tunnel

The default tunnel executable location is:

```text
tools\tunnel-client-runtime-cloudflared.exe
```

The executable is a local tool and is ignored by Git. Override its location
with either:

```powershell
.\scripts\start-sceneworks.ps1 -TunnelClientPath C:\path\to\tunnel-client-runtime-cloudflared.exe
```

or:

```powershell
$env:SCENEWORKS_TUNNEL_CLIENT_PATH = "C:\path\to\tunnel-client-runtime-cloudflared.exe"
```

The launcher starts the tunnel only when both credentials are available:

```text
CONTROL_PLANE_TUNNEL_ID
CONTROL_PLANE_API_KEY
```

The MCP target defaults to:

```text
http://127.0.0.1:8010/mcp
```

Override it with `MCP_SERVER_URL` or `-McpServerUrl` when necessary.

If the tunnel executable or credentials are missing, SceneWorks itself still
starts and the launcher emits a warning. If the tunnel readiness endpoint is
already reachable, the existing tunnel is reused instead of starting another
one.

The launcher likewise does not duplicate existing SceneWorks services: if the
API or frontend health URL is already reachable, that service is reused.

See [the ChatGPT MCP plugin tutorial](../tutorials/chatgpt-mcp-plugin.md) for
first-time tunnel creation and plugin configuration.
