# Connect SceneWorks to ChatGPT with the MCP plugin

This tutorial connects ChatGPT to SceneWorks' MCP endpoint. Start in
**Observe** mode, verify semantic reads, then move to **Standard** or
**Advanced** only when you need more authority.

## 1. Start SceneWorks and verify MCP locally

On Windows, the normal launcher is:

```powershell
.\scripts\start-sceneworks.cmd
```

The backend MCP endpoint is:

```text
http://127.0.0.1:8010/mcp
```

Verify it independently before involving the tunnel:

```powershell
Invoke-RestMethod http://127.0.0.1:8010/mcp
```

Expected shape:

```json
{
  "name": "SceneWorks",
  "endpoint": "/mcp",
  "transport": "streamable HTTP / JSON-RPC",
  "mode": "observe",
  "action_tools_enabled": false,
  "advanced_agent_sessions_enabled": false
}
```

`POST /mcp` is the protocol endpoint used by ChatGPT. `GET /mcp` is only a
setup/health check.

## 2. Leave SceneWorks in Observe mode first

The default is:

```env
SCENEWORKS_MCP_ENABLED=true
SCENEWORKS_MCP_MODE=observe
```

You can also select **Settings -> ChatGPT / MCP -> Observe** in the SceneWorks
web UI.

Observe exposes semantic reads only: projects, tasks, accepted memory,
artifacts, diffs, and execution evidence. It cannot launch Gemini or mutate
SceneWorks.

## 3. Create the ChatGPT plugin

In ChatGPT's custom MCP/plugin area:

1. Choose **New Plugin**.
2. Name it `SceneWorks`.
3. Suggested description:

   ```text
   SceneWorks engineering control plane: projects, tasks, evidence, memory, governed workflows and optional Gemini execution sessions.
   ```

4. For a local/private SceneWorks instance, choose **Connection -> Tunnel**.

Do not use a public Server URL just to reach `localhost`.

## 4. Create and run the Secure MCP Tunnel

There are two separate pieces:

```text
ChatGPT tunnel object
        |
        | secure outbound tunnel
        v
local tunnel-client process
        |
        v
http://127.0.0.1:8010/mcp
```

Creating the tunnel in ChatGPT does **not** ask for the local MCP destination.
The local destination is configured in the tunnel-client process.

### First-time setup

1. In ChatGPT, create a tunnel such as `SceneWorks Tunnel`.
2. Copy its `tunnel_...` ID.
3. Create a restricted OpenAI runtime API key with the tunnel permissions
   needed to read/use that tunnel.
4. Download `tunnel-client-runtime-cloudflared.exe`.
5. Put it at the default local path:

   ```text
   <repo>\tools\tunnel-client-runtime-cloudflared.exe
   ```

   The executable is intentionally Git-ignored. To keep it elsewhere, either:

   ```powershell
   $env:SCENEWORKS_TUNNEL_CLIENT_PATH = "C:\path\to\tunnel-client-runtime-cloudflared.exe"
   ```

   or pass:

   ```powershell
   .\scripts\start-sceneworks.cmd -TunnelClientPath C:\path\to\tunnel-client-runtime-cloudflared.exe
   ```

6. Persist the non-secret tunnel ID for your Windows user:

   ```powershell
   [Environment]::SetEnvironmentVariable(
       "CONTROL_PLANE_TUNNEL_ID",
       "tunnel_...",
       "User"
   )
   ```

7. Make `CONTROL_PLANE_API_KEY` available to the launcher. Treat it as a
   secret; do not commit it or put it in repository files.

8. The MCP target defaults to:

   ```text
   http://127.0.0.1:8010/mcp
   ```

   Override only when necessary with `MCP_SERVER_URL` or `-McpServerUrl`.

### Normal startup

After first-time setup, one command starts SceneWorks and the tunnel:

```powershell
.\scripts\start-sceneworks.cmd
```

The launcher:

1. starts/reuses the backend;
2. waits for `/api/health`;
3. starts/reuses the frontend;
4. verifies the MCP endpoint;
5. starts the tunnel client in a separate PowerShell window;
6. waits for `http://127.0.0.1:8080/readyz`.

If the tunnel credentials or executable are missing, SceneWorks still starts
and the launcher prints a warning. Use `-NoTunnel` when you intentionally do
not want ChatGPT connectivity.

A tunnel log containing both of these is a healthy sign:

```text
mcp session initialized ... server_name=SceneWorks
...
tunnel metadata fetched ...
```

The tunnel client may also emit an OAuth discovery warning. SceneWorks is not
an OAuth provider, so this warning is expected for the local tunnel setup.

Do **not** expose port `8010` publicly. SceneWorks is a single-user local
control plane and the bare FastAPI service does not implement OAuth/user
authentication.

## 5. Select the running tunnel in ChatGPT

Keep the tunnel client running. Return to the plugin configuration and select
`SceneWorks Tunnel` under **Connection -> Tunnel**.

If the tunnel exists in the OpenAI tunnel console but does not appear in the
ChatGPT plugin selector, confirm it is associated with the ChatGPT workspace
that is creating the plugin, then reopen the plugin dialog after propagation.

## 6. Scan the Observe tool catalogue

Before accepting the custom MCP warning:

- verify the endpoint/tunnel is yours;
- keep SceneWorks in Observe mode;
- scan/review the tools;
- confirm no generic host primitives appear.

Expected Observe tools:

```text
sceneworks.capabilities
sceneworks.list_projects
sceneworks.get_project_context
sceneworks.list_tasks
sceneworks.get_task
sceneworks.get_task_diff
sceneworks.get_execution
sceneworks.search_memory
sceneworks.list_artifacts
```

It should **not** expose generic host primitives such as:

```text
read_file
write_file
raw_shell
git_command
git_push
sql
```

If generic host tools appear, stop: that is not the intended SceneWorks MCP
interface.

## 7. Test Observe mode from ChatGPT

Try:

```text
@SceneWorks list my projects and tell me which have active tasks.
```

Then:

```text
@SceneWorks inspect the project context for <project>. Separate accepted project memory from agent output and tell me what is authoritative.
```

Then:

```text
@SceneWorks review task <id> using its contract, architecture, implementation evidence, review result and Git provenance. Do not trust the implementation summary when evidence disagrees.
```

At this stage ChatGPT reasons over SceneWorks state but cannot launch agents.

## 8. Move to Standard mode for governed engineering actions

In SceneWorks:

```text
Settings -> ChatGPT / MCP -> Standard
```

or configure:

```env
SCENEWORKS_MCP_MODE=standard
```

Standard adds:

```text
sceneworks.inspect_repository
sceneworks.ask_role
sceneworks.create_task
sceneworks.task_action
```

Refresh/rescan the plugin if ChatGPT does not immediately see the changed tool
catalogue.

A useful repository-read test is:

```text
@SceneWorks inspect the repository for <project> and find where task scheduling is implemented. Give file/symbol evidence and the current commit. Do not modify anything.
```

A governed task can then be created with an explicit engineering contract and
started through the normal SceneWorks workflow. SceneWorks remains workflow
authority; ChatGPT is not given raw shell or raw repository primitives through
MCP.

## 9. Use Advanced mode only when ChatGPT should supervise Gemini directly

Advanced mode lets ChatGPT run an iterative session in which Gemini CLI acts as
an execution subagent.

Before enabling it, open:

```text
Settings -> ChatGPT / MCP -> Advanced
```

Review the Advanced-session capability ceiling. Typical capabilities are:

```text
Repository read
Repository write
Shell / tests
Git commit
Web / network
Gemini native subagents
```

Disable anything you do not need. The server allowlist is a ceiling: an
individual Advanced session can request only a subset.

### Shell boundary

File reads/writes mediated by ACP are confined to the session's isolated
worktree. Shell is different: SceneWorks constrains the terminal cwd, but the
shell process still runs with the operating-system authority of the SceneWorks
user. This is **not an OS/container sandbox**.

## 10. Test a read-only Advanced session first

Ask ChatGPT:

```text
@SceneWorks create an Advanced Gemini session for <project> with repository_read only. Investigate the architecture around <problem> and report evidence. Do not modify anything.
```

ChatGPT should use:

```text
sceneworks.agent_session.create
sceneworks.agent_session.prompt
sceneworks.agent_session.get
```

The first creation calls Gemini ACP `session/new`. Later prompts use a fresh
Gemini process and `session/load` with the same provider conversation and
isolated worktree.

## 11. Controlled Advanced implementation

For a bounded example:

```text
@SceneWorks create an Advanced session for <project> with repository_read, repository_write, shell_execute and git_commit. First investigate <bug>; do not edit until you have a root-cause hypothesis.
```

After Gemini reports back:

```text
Test that hypothesis. If confirmed, implement the smallest compatible fix and run the targeted tests.
```

Then inspect the real diff rather than trusting prose:

```text
Show me the actual session diff and remaining worktree status. Do not rely on Gemini's prose summary.
```

ChatGPT should call:

```text
sceneworks.agent_session.diff
```

When finished, close the session and explicitly decide whether to preserve its
branch/worktree for human review.

## 12. Independent review after Advanced execution

Advanced mode intentionally gives ChatGPT more direct supervision, but Gemini's
result is still not authoritative. A useful finishing loop is:

1. inspect `agent_session.diff`;
2. use a normal SceneWorks review task/Reviewer when independent verification is valuable;
3. compare tests/evidence against project policy and accepted memory;
4. integrate only after human review.

## Dashboard behavior

Installing MCP does not redirect dashboard requests to ChatGPT:

```text
Dashboard request -> SceneWorks workflow -> configured AgentBackend
```

ChatGPT intervenes only when you invoke SceneWorks from ChatGPT.

## Troubleshooting

### `GET /mcp` returns 404

Enable MCP in Settings or set:

```env
SCENEWORKS_MCP_ENABLED=true
```

### Launcher says tunnel client is missing

Put the executable at:

```text
tools\tunnel-client-runtime-cloudflared.exe
```

or configure `SCENEWORKS_TUNNEL_CLIENT_PATH` / `-TunnelClientPath`.

### Launcher says tunnel credentials are missing

Set `CONTROL_PLANE_TUNNEL_ID` and make `CONTROL_PLANE_API_KEY` available to the
launcher process. Do not store the API key in Git.

### ChatGPT cannot reach localhost

Use **Tunnel / Secure MCP Tunnel** instead of a public Server URL.

### OAuth discovery warning

Expected for the local tunnel path. SceneWorks itself is not an OAuth provider.

### I only see read tools

SceneWorks is in Observe mode. Select Standard or Advanced and refresh/rescan the
plugin tool catalogue.

### Advanced tools return a mode error

Set:

```env
SCENEWORKS_MCP_MODE=advanced
```

or choose Advanced in the SceneWorks Settings page.

### Advanced session fails with `loadSession`

The installed Gemini CLI did not advertise/support ACP persistent-session
loading. Upgrade/verify Gemini CLI. SceneWorks fails closed instead of silently
starting a context-less replacement conversation.

### A permission is rejected when creating an Advanced session

It is not present in the configured Advanced-session capability ceiling. Review
**Settings -> ChatGPT / MCP** rather than widening it automatically.

### A large result is truncated

Adjust cautiously:

```env
SCENEWORKS_MCP_TOOL_MAX_CHARS=200000
```

### Tool list changed after a SceneWorks update

Refresh/rescan the plugin and review newly added action definitions before
raising SceneWorks' operating mode.

## Security checklist

Before leaving Standard/Advanced enabled:

- SceneWorks is still local/private or protected by authenticated TLS.
- The plugin points to the expected SceneWorks tunnel/endpoint.
- No generic host primitive MCP tools are present.
- Observe was tested before raising authority.
- Advanced capability ceiling is no broader than needed.
- You understand shell is not an OS sandbox.
- Human integration/acceptance remains explicit.

See `docs/wp11-mcp-reasoning-interface.md` and
`docs/gemini-capability-matrix.md` for the architectural/security boundary.
