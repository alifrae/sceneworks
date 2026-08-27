# Connect SceneWorks to ChatGPT with the MCP plugin

This tutorial connects ChatGPT to the WP11 SceneWorks MCP endpoint so ChatGPT
can inspect SceneWorks state directly and, when you explicitly enable actions,
use SceneWorks to invoke Gemini CLI roles and control engineering tasks.

The names in ChatGPT may move as the Plugins/MCP UI evolves. The current setup
screen contains the fields **Name**, **Description**, **Connection** with
**Server URL / Tunnel**, **Authentication**, the custom-MCP risk acknowledgement,
and **Create**.

## 1. Start SceneWorks locally

From the repository, start the backend normally. The default address is:

```text
http://127.0.0.1:8010
```

Verify WP11 is reachable in a browser or with curl:

```bash
curl http://127.0.0.1:8010/mcp
```

Expected high-level response:

```json
{
  "name": "SceneWorks",
  "endpoint": "/mcp",
  "transport": "streamable HTTP / JSON-RPC",
  "action_tools_enabled": false
}
```

The actual MCP endpoint used by ChatGPT is:

```text
http://127.0.0.1:8010/mcp
```

`POST /mcp` is the protocol endpoint; `GET /mcp` above is only a setup check.

## 2. Start read-only first

The safe default is already:

```env
SCENEWORKS_MCP_ENABLED=true
SCENEWORKS_MCP_ALLOW_ACTIONS=false
```

With this setting ChatGPT can use semantic read tools such as project/task,
memory, artifact, diff and execution inspection, but SceneWorks refuses requests
to create tasks, start Gemini roles or change task state.

Do **not** enable actions until the MCP connection itself works and you have
reviewed the discovered tool list.

## 3. Open the custom plugin setup in ChatGPT

In ChatGPT:

1. Open **Plugins** (from the sidebar/plugin directory or Settings, depending on
   the current UI).
2. Choose the option to create/add a custom plugin/MCP connection.
3. The **New Plugin** dialog should open.
4. Set **Name** to `SceneWorks`.
5. Suggested description:

   ```text
   SceneWorks engineering control plane: projects, tasks, evidence, memory and governed agent workflows.
   ```

An icon is optional.

## 4. Choose Tunnel for a local/private SceneWorks

ChatGPT does not connect directly to a localhost MCP server. For SceneWorks
running on your laptop or private network, select:

```text
Connection -> Tunnel
```

Use ChatGPT's **Secure MCP Tunnel** flow to make the local MCP target available
to ChatGPT without publishing port `8010` to the public internet. When the
tunnel asks for the local target, use:

```text
http://127.0.0.1:8010/mcp
```

Follow the tunnel UI until it reports the connection as available, then return
to the New Plugin dialog and continue the tool scan/creation flow.

### Do not solve localhost connectivity by opening port 8010 publicly

SceneWorks is deliberately a single-user local control plane. The FastAPI API
and `/mcp` endpoint do not implement user authentication or OAuth. Publicly
forwarding `8010` would expose the same task-control API the dashboard uses.

## 5. Server URL is for a properly protected remote deployment

Use **Server URL** only when SceneWorks is behind infrastructure you control,
for example:

```text
https://sceneworks.example.net/mcp
```

The external endpoint must terminate HTTPS and enforce authentication before
traffic reaches SceneWorks.

### Authentication field

SceneWorks WP11 itself is **not an OAuth provider**.

Therefore:

- with **Tunnel**, use the authentication/tunnel mechanism provided by the
  ChatGPT tunnel flow;
- with **Server URL + OAuth**, configure OAuth on the trusted reverse proxy or
  identity-aware gateway in front of SceneWorks, not inside SceneWorks;
- if the UI offers a no-authentication option, only use it for a connection that
  is already private/protected by the tunnel or equivalent trusted boundary;
- do not select OAuth and point it directly at bare `http://127.0.0.1:8010/mcp`;
  SceneWorks has no OAuth discovery/token endpoints to satisfy that flow.

## 6. Acknowledge the custom MCP warning

The New Plugin dialog warns that an unreviewed MCP server may expose data or
cause unintended actions.

For your own SceneWorks server:

1. verify the connection points to the SceneWorks instance you control;
2. leave `SCENEWORKS_MCP_ALLOW_ACTIONS=false` for the first scan;
3. review the tool list described below;
4. tick **I understand and want to continue**;
5. use **Scan Tools** if shown by the current UI, then choose **Create**.

If ChatGPT shows a diff when tools change later, review it rather than blindly
enabling newly discovered actions.

## 7. Verify the discovered tools

The plugin should discover tools with the `sceneworks.` prefix. The important
read-only tools are:

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

It should **not** expose generic machine tools such as:

```text
read_file
write_file
shell
git_commit
git_push
sql
```

If those generic tools appear, stop and inspect the server you connected to; that
is not the intended WP11 interface.

The action tools may also appear in discovery but SceneWorks rejects them until
its server-side action gate is enabled:

```text
sceneworks.inspect_repository
sceneworks.ask_role
sceneworks.create_task
sceneworks.task_action
```

## 8. Test read-only grounding from ChatGPT

In a new ChatGPT conversation, select/mention the SceneWorks plugin for the
message. Depending on the current UI this can be done from the tool picker or by
mentioning the plugin.

Try:

```text
@SceneWorks list my SceneWorks projects and tell me which ones have active tasks.
```

Then:

```text
@SceneWorks inspect the project context for <project>. Separate accepted project memory from agent output and tell me what is currently authoritative.
```

Then:

```text
@SceneWorks review task <id>. Use the task contract, architecture, implementation evidence, review result and Git provenance. Do not assume the implementation summary is correct if the diff/evidence disagrees.
```

At this stage ChatGPT can reason over SceneWorks state but cannot start Gemini or
change task state.

## 9. Enable master-brain actions

After the read-only connection works, edit `backend/.env`:

```env
SCENEWORKS_MCP_ALLOW_ACTIONS=true
```

Restart the SceneWorks backend.

Refresh/rescan the custom plugin in ChatGPT if the UI requires it. The action
tool definitions are the same, but the SceneWorks server-side policy now allows
them to execute.

This flag is the final authority gate. A ChatGPT-side approval prompt is useful,
but SceneWorks does not rely on it for whether MCP actions are globally allowed.

## 10. Test Gemini CLI as ChatGPT's repository eyes

Ask:

```text
@SceneWorks inspect the repository for <project> and determine where task scheduling is implemented. I need file/symbol evidence and the current commit, but do not modify anything.
```

ChatGPT should use:

```text
sceneworks.inspect_repository
```

SceneWorks starts the **Technical Expert** role through the configured
AgentBackend (normally Gemini CLI over ACP) on a detached commit-pinned worktree.
The Technical Expert can inspect/read and use shell commands, but cannot modify
repository source or create Git commits.

The tool returns an execution id. ChatGPT can poll:

```text
sceneworks.get_execution
```

until the inspection completes, then reason over the returned evidence.

## 11. Test task creation and implementation

A useful high-level request is:

```text
@SceneWorks create a bounded task for project <project> to fix <bug>.
First inspect the relevant repository area if needed. Put concrete allowed_scope,
required_tests and acceptance_criteria in the engineering contract. Do not start
implementation until you show me the task contract.
```

After reviewing it, tell ChatGPT:

```text
@SceneWorks start the architecture/workflow for that task.
```

SceneWorks, not ChatGPT, owns the workflow state.

For a bounded non-high-priority bug with an explicit scope, required tests and
acceptance criteria, WP11 may deterministically skip a redundant Architect and
run:

```text
Triage -> optional advisers -> Engineer -> Reviewer -> human
```

For unbounded, high-risk or cross-cutting work, SceneWorks retains the normal
architecture and approval path.

The Engineer remains the coding **hands**:

```text
SceneWorks -> AgentBackend -> Gemini CLI / ACP -> isolated branch worktree
```

ChatGPT never receives raw shell or file-write MCP tools.

## 12. Independent review loop

After Gemini finishes, ask:

```text
@SceneWorks independently review task <id> for me. Compare the original contract,
accepted project memory, architecture if present, actual Git diff/result commit,
test/reviewer evidence and execution history. Identify unsupported claims or
remaining risks before I accept it.
```

This is the main WP11 value: no manual copying of Gemini's completion report back
into ChatGPT. Both the dashboard and ChatGPT read the same persisted task and
execution state.

## Dashboard behavior after MCP is installed

Nothing is automatically redirected to ChatGPT.

A request made only in the SceneWorks dashboard follows the SceneWorks workflow
and configured AgentBackend. ChatGPT intervenes only when you invoke the
SceneWorks plugin from a ChatGPT message.

This is deliberate: SceneWorks stays usable if ChatGPT is unavailable, and
trivial/background workflows do not pay an extra remote reasoning round-trip.

## Troubleshooting

### `GET /mcp` returns 404

Check:

```env
SCENEWORKS_MCP_ENABLED=true
```

Restart the backend.

### ChatGPT cannot reach `127.0.0.1`

That is expected without a tunnel. Select **Tunnel / Secure MCP Tunnel** rather
than **Server URL** for a local machine.

### OAuth discovery fails

You pointed the OAuth flow at SceneWorks directly. SceneWorks does not implement
OAuth. Use the tunnel flow or put an OAuth-capable authenticated gateway in front
of a remote SceneWorks deployment.

### Read tools work but create/inspect/actions return an error

Check:

```env
SCENEWORKS_MCP_ALLOW_ACTIONS=true
```

and restart SceneWorks.

### A large diff/artifact is truncated

Raise cautiously:

```env
SCENEWORKS_MCP_TOOL_MAX_CHARS=200000
```

The default response bound exists to prevent one MCP call from flooding the
reasoning context.

### Tool list changed after a SceneWorks update

Use ChatGPT's refresh/rescan control, review the changed action definitions, then
enable them deliberately. Do not assume a previously reviewed plugin remains
identical after its MCP server changes.

## Security checklist

Before leaving MCP actions enabled:

- SceneWorks still binds to `127.0.0.1` unless you deliberately changed it.
- Local/private access uses Secure MCP Tunnel rather than public port forwarding.
- A remote URL is HTTPS + authenticated before reaching FastAPI.
- The plugin points to the expected SceneWorks endpoint.
- No generic shell/filesystem/Git MCP tools are present.
- The action list contains only semantic SceneWorks operations.
- Human acceptance remains required before integrating agent work.
- You understand that ACP workspace confinement is not an OS/container sandbox;
  consult `docs/gemini-capability-matrix.md` for the exact Gemini boundary.

For the architecture and tool contract, see
`docs/wp11-mcp-reasoning-interface.md`.
