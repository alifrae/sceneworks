# Connect SceneWorks to ChatGPT with the MCP plugin

This tutorial connects ChatGPT to SceneWorks' WP11 MCP endpoint. Start in
**Observe** mode, verify the semantic read tools, then opt into **Standard** or
**Advanced** only when you need their additional authority.

The exact ChatGPT labels may move as the Plugins/MCP UI evolves. The current
custom setup exposes **Name**, **Description**, **Connection** (Server URL or
Tunnel), **Authentication**, a custom-MCP risk acknowledgement and **Create**.

## 1. Start SceneWorks and verify MCP locally

Default backend address:

```text
http://127.0.0.1:8010
```

Open or curl:

```bash
curl http://127.0.0.1:8010/mcp
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

The protocol endpoint is:

```text
http://127.0.0.1:8010/mcp
```

`POST /mcp` is used by ChatGPT. `GET /mcp` is only a setup/health check.

## 2. Leave SceneWorks in Observe mode first

The default is:

```env
SCENEWORKS_MCP_ENABLED=true
SCENEWORKS_MCP_MODE=observe
```

You can also select **Settings -> ChatGPT / MCP -> Observe** in the SceneWorks
web UI.

Observe exposes only semantic reads: projects, tasks, accepted memory, artifacts,
diffs and execution evidence. It cannot launch Gemini or mutate SceneWorks.

## 3. Add the custom MCP plugin in ChatGPT

In ChatGPT:

1. Open **Plugins** / the custom MCP integration area.
2. Choose **New Plugin** / add custom plugin.
3. Name it `SceneWorks`.
4. Suggested description:

   ```text
   SceneWorks engineering control plane: projects, tasks, evidence, memory, governed workflows and optional Gemini execution sessions.
   ```

5. Leave the plugin in the risk-review flow until the connection below works.

## 4. Use Tunnel for a local/private SceneWorks

For SceneWorks running on your laptop/private network, choose:

```text
Connection -> Tunnel
```

Use ChatGPT's Secure MCP Tunnel flow and point the local target at:

```text
http://127.0.0.1:8010/mcp
```

Do **not** solve localhost connectivity by exposing port `8010` publicly.
SceneWorks is a single-user local control plane and the bare FastAPI service does
not implement OAuth/user authentication.

## 5. Use Server URL only behind authenticated infrastructure

For a remote deployment, the externally visible target can look like:

```text
https://sceneworks.example.net/mcp
```

Terminate HTTPS and enforce authentication before traffic reaches SceneWorks.
If ChatGPT asks for OAuth, OAuth belongs in that trusted gateway; SceneWorks
WP11 itself is not an OAuth provider.

## 6. Acknowledge the MCP warning and scan tools

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

It should **not** expose tools such as:

```text
read_file
write_file
raw_shell
git_command
git_push
sql
```

If generic host tools appear, stop: that is not the intended WP11 interface.

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

### Test Gemini as repository eyes

Ask:

```text
@SceneWorks inspect the repository for <project> and find where task scheduling is implemented. Give file/symbol evidence and the current commit. Do not modify anything.
```

SceneWorks starts the Technical Expert through Gemini CLI/ACP on a detached,
commit-pinned worktree. ChatGPT can poll `sceneworks.get_execution` and reason
over the result.

### Test a governed task

Ask:

```text
@SceneWorks create a bounded task for project <project> to fix <bug>. Inspect first if needed. Put explicit allowed_scope, required_tests and acceptance_criteria in the engineering contract. Show me the contract before starting work.
```

Then:

```text
@SceneWorks start the workflow for that task.
```

SceneWorks remains the workflow authority. A tightly bounded non-high-priority
bug can skip Architect deterministically, but Engineer -> Reviewer remains.
Riskier work keeps architecture/approval.

## 9. Use Advanced mode when ChatGPT should be the supervisor

Advanced mode is different from Standard. It deliberately lets ChatGPT run an
iterative loop in which Gemini CLI acts as an execution subagent.

Before enabling it, open:

```text
Settings -> ChatGPT / MCP -> Advanced
```

Review the **Advanced-session capability ceiling**. Typical capabilities are:

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

### Important shell warning

File reads/writes mediated by ACP are confined to the session's isolated
worktree. Shell is different: SceneWorks constrains the terminal cwd, but the
shell process still runs with the operating-system authority of the SceneWorks
user. This is **not an OS/container sandbox**.

Enable Advanced shell/network only under your own responsibility.

## 10. Test a read-only Advanced Gemini session

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

The first session creation calls Gemini ACP `session/new`. Later prompts use a
fresh Gemini process and `session/load` with the same provider conversation and
same isolated worktree. This keeps Gemini context without keeping one process
alive indefinitely.

The MCP response does not expose the absolute worktree path or Gemini's raw
provider session id.

## 11. Test iterative Advanced implementation

For a controlled example:

```text
@SceneWorks create an Advanced session for <project> with repository_read, repository_write, shell_execute and git_commit. First investigate <bug>; do not edit until you have a root-cause hypothesis.
```

After Gemini reports back, continue in the same ChatGPT conversation:

```text
Test that hypothesis. If confirmed, implement the smallest compatible fix and run the targeted tests.
```

Then:

```text
Show me the actual session diff and remaining worktree status. Do not rely on Gemini's prose summary.
```

ChatGPT should call:

```text
sceneworks.agent_session.diff
```

You can continue prompting the same persistent session for additional evidence,
tests or corrections.

When finished:

```text
@SceneWorks close that Advanced session, but preserve its branch/worktree if it contains work I still need to review.
```

## 12. Web search/fetch and Gemini subagents

When `network_access`/`subagents` are allowed, Gemini may use its native tools
such as web search/fetch and native subagents (for example a codebase
investigator, depending on installed Gemini CLI version).

These are provider-native capabilities rather than separate raw MCP tools.
SceneWorks records the capabilities Gemini advertises at session creation and
gates visible ACP permission requests, but it does not claim a complete network
sandbox around every provider-internal tool implementation.

If a task does not need external research or subagents, leave those permissions
off.

## 13. Independent review after Advanced execution

Advanced mode intentionally gives ChatGPT more direct supervision, but it does
not make Gemini's result authoritative.

A useful finishing loop is:

1. inspect `agent_session.diff`;
2. create/use a normal SceneWorks review task or Reviewer when independent
   verification is valuable;
3. compare tests/evidence against project policy and accepted memory;
4. integrate only after human review.

## Dashboard behavior

Installing MCP does not redirect dashboard requests to ChatGPT.

```text
Dashboard request -> SceneWorks workflow -> configured AgentBackend
```

ChatGPT intervenes only when you invoke SceneWorks from ChatGPT. This keeps
SceneWorks autonomous and avoids paying an extra reasoning round-trip for every
operation.

## Troubleshooting

### `GET /mcp` returns 404

Enable MCP in Settings or:

```env
SCENEWORKS_MCP_ENABLED=true
```

### ChatGPT cannot reach localhost

Use **Tunnel / Secure MCP Tunnel** for a local/private SceneWorks instead of a
public Server URL.

### OAuth discovery fails

SceneWorks is not an OAuth provider. Use the tunnel flow or put an OAuth-capable
authenticated gateway in front of a remote deployment.

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
loading. Upgrade/verify Gemini CLI. WP11 fails closed instead of silently
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
- The plugin points to the expected SceneWorks endpoint.
- No generic host primitive MCP tools are present.
- Observe was tested before raising authority.
- Advanced capability ceiling is no broader than needed.
- You understand shell is not an OS sandbox.
- Human integration/acceptance remains explicit.

See `docs/wp11-mcp-reasoning-interface.md` and
`docs/gemini-capability-matrix.md` for the architectural/security boundary.
