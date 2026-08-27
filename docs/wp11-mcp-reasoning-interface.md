# WP11 — MCP Reasoning Interface

## Goal

Make SceneWorks a first-class reasoning/control surface for external clients such
as ChatGPT while keeping engineering execution inside SceneWorks' project,
worktree, permission, workflow and provenance boundaries.

The governing rule is:

> **MCP exposes SceneWorks capabilities, not the underlying machine.**

There are intentionally no MCP tools equivalent to raw `read_file`,
`write_file`, `shell`, `git_command`, `git_push` or SQL execution.

## Architecture

```text
                                  human
                                    │
                 ┌──────────────────┴─────────────────┐
                 │                                    │
          SceneWorks dashboard                  ChatGPT / MCP client
                 │                                    │
                 │                                 MCP /mcp
                 │                                    │
                 └──────────────► SceneWorks ◄────────┘
                                  control plane
                                       │
              ┌────────────────────────┴────────────────────────┐
              │                                                 │
       governed workflow                              Advanced session
              │                                                 │
       roles + approvals                                  ChatGPT supervisor
              │                                                 │
        Gemini CLI / ACP                               Gemini CLI / ACP
              │                                                 │
   isolated task worktrees                    persistent conversation +
                                               isolated session worktree
```

### Responsibility split

| Component | Responsibility |
|---|---|
| Human | Intent, project truth, mode/permission policy, final acceptance |
| ChatGPT / MCP client | Higher-level reasoning, evidence synthesis, task control; in Advanced mode, iterative Gemini supervision |
| SceneWorks | Source of truth, MCP policy, worktrees, workflows, project memory, audit/provenance and lifecycle |
| Gemini CLI over ACP | Repository/tool execution: inspection, edits, shell/tests, native provider capabilities |
| Reviewer | Independent verification for governed task workflows |

ChatGPT remains optional. Dashboard workflows continue to work without it.

## Three MCP operating modes

The mode is persisted in SceneWorks Settings and can also be set through
`SCENEWORKS_MCP_MODE`.

### Observe — default

```text
ChatGPT -> semantic SceneWorks reads
```

Available operations are project/task/context/memory/artifact/execution reads.
No agent execution or SceneWorks mutation can be launched.

Use Observe when first connecting a new ChatGPT plugin/tunnel.

### Standard

```text
ChatGPT -> SceneWorks governed action -> roles/workflow -> Gemini CLI
```

Standard adds:

- `sceneworks.inspect_repository`;
- `sceneworks.ask_role`;
- `sceneworks.create_task`;
- `sceneworks.task_action`.

ChatGPT controls SceneWorks semantically, but SceneWorks still decides the
workflow. Engineer/Reviewer permissions and human approvals remain intact.

### Advanced

```text
ChatGPT
   │ supervisor instructions
   ▼
SceneWorks Advanced agent session
   │ ACP session/new + session/load
   ▼
Gemini CLI execution subagent
   ├── repository reads
   ├── edits (when allowed)
   ├── shell/tests (when allowed)
   ├── Git commits (when allowed)
   ├── web/network (when allowed/provider supports it)
   └── Gemini native subagents (when allowed/provider supports them)
```

Advanced mode is the deliberate escape hatch for the workflow where ChatGPT is
the master reasoning agent and Gemini CLI is its execution subagent.

It does **not** bypass SceneWorks entirely. SceneWorks still creates the isolated
worktree, records base/branch provenance, applies the configured capability
ceiling, persists session state and supports cancellation/diff inspection.

## Persistent Gemini sessions

Advanced sessions are **provider-persistent but process-stateless**.

SceneWorks stores:

- project id;
- exact base commit;
- isolated worktree/branch;
- Gemini ACP provider session id;
- selected model;
- effective capability list;
- capabilities advertised by Gemini during ACP initialization;
- latest turn result/error.

A new Advanced session calls `session/new`. Each later supervisor turn starts a
fresh Gemini ACP process, calls `session/load` with the same provider session id
and worktree, then calls `session/prompt`.

This design keeps iterative Gemini context while avoiding a permanently running
Gemini process. It also allows SceneWorks to reconcile a session after its own
process restarts. Advanced mode fails closed if the installed Gemini CLI does not
advertise ACP `loadSession` support.

The Gemini model is selected when the SceneWorks session is created (or inherited
from `SCENEWORKS_GEMINI_MODEL`). WP11 does not depend on unstable dynamic ACP
model switching.

SceneWorks supplies `mcpServers: []` to Gemini ACP in WP11. It does not
recursively inject the SceneWorks MCP server or arbitrary third-party MCP
servers into Gemini.

## Advanced MCP tools

Available only in Advanced mode:

| Tool | Purpose |
|---|---|
| `sceneworks.agent_session.list` | List Advanced sessions |
| `sceneworks.agent_session.get` | Status, capabilities and latest result; no host paths |
| `sceneworks.agent_session.create` | Create persistent Gemini session + isolated worktree |
| `sceneworks.agent_session.prompt` | Send next supervisor instruction asynchronously |
| `sceneworks.agent_session.diff` | Inspect Git diff/commits/worktree status |
| `sceneworks.agent_session.cancel` | Cancel current turn while retaining session/worktree |
| `sceneworks.agent_session.close` | Close session; optionally clean a clean worktree |

The external client never receives the raw provider session id or the absolute
worktree/repository path through MCP.

## Gemini capability policy

SceneWorks distinguishes provider capability from SceneWorks permission.

```text
capability available in installed Gemini CLI
                    ∩
SceneWorks Advanced capability ceiling
                    ∩
permissions requested for this session
                    =
effective session authority
```

Advanced-session policy keys are:

- `repository_read`;
- `repository_write`;
- `shell_execute`;
- `git_commit`;
- `network_access`;
- `subagents`.

### What SceneWorks can strongly mediate

The ACP client directly proxies:

- text-file reads;
- text-file writes;
- terminal creation/output/wait/kill;
- ACP permission requests.

File paths are checked against the isolated worktree.

### What remains provider/OS dependent

Gemini's native web search/fetch and subagents are useful in Advanced mode, but
not every provider-internal tool path is guaranteed to cross a SceneWorks ACP
permission request in every Gemini CLI version. SceneWorks denies the visible
network/subagent permission requests when those capabilities are disabled and
blocks obvious network commands in its terminal proxy, but this is **not a
network sandbox**.

Similarly, shell execution is cwd-gated to the worktree when created through the
ACP terminal proxy, but the subprocess still has the operating-system authority
of the SceneWorks user. A shell can in principle access resources outside the
worktree unless an external OS/container sandbox prevents it.

Therefore Advanced mode is an explicit operator-responsibility mode, not a claim
of hostile-code isolation.

See `docs/gemini-capability-matrix.md` and `docs/limitations.md`.

## Read-only semantic tool surface

Available in every mode:

| Tool | Purpose |
|---|---|
| `sceneworks.capabilities` | Current MCP mode, roles, ACP boundary and Advanced allowlist |
| `sceneworks.list_projects` | Projects/current activity |
| `sceneworks.get_project_context` | Project policy, verification commands, Git snapshot, tasks and accepted memory |
| `sceneworks.list_tasks` | Task discovery |
| `sceneworks.get_task` | Contract, architecture, results, executions, events and provenance |
| `sceneworks.get_task_diff` | Git-observed task diff/commits/status |
| `sceneworks.get_execution` | Governed execution result/events without host paths |
| `sceneworks.search_memory` | Project memory; `accepted` is default |
| `sceneworks.list_artifacts` | Stored role/execution artifacts |

Large responses are bounded by `SCENEWORKS_MCP_TOOL_MAX_CHARS`.

## Adaptive governed-role routing

WP11 also avoids paying the Architect latency for tightly bounded bugs.

A task may skip Architect only when all are true:

1. Triage classifies it as `bug`.
2. It requires implementation.
3. Priority is not `high`.
4. Engineering contract has non-empty `allowed_scope`.
5. It has non-empty `required_tests`.
6. It has non-empty `acceptance_criteria`.

The audited transition is:

```text
ARCHITECTURE_ANALYSIS --skip_architecture--> READY_TO_IMPLEMENT
```

The task still continues through Engineer -> Reviewer. For unbounded/risky work,
an LLM `use_architect=false` result is overridden by the deterministic policy.

## Memory/evidence authority

WP11 preserves the knowledge firewall:

- accepted Project Memory is authoritative project knowledge;
- proposed memory is not silently treated as truth;
- Gemini/role responses are execution evidence/inference;
- Git remains authoritative for what changed;
- human acceptance remains explicit.

Advanced mode does not automatically promote Gemini/ChatGPT conclusions into
accepted Project Memory.

## Security and connection model

SceneWorks remains a single-user local control plane and does not add OAuth to
`/mcp`.

- Keep SceneWorks bound to `127.0.0.1` by default.
- Use ChatGPT Secure MCP Tunnel for a local/private deployment where supported.
- Never publish the bare `:8010` control-plane port directly to the internet.
- If using a remote Server URL, put authenticated TLS infrastructure in front.
- Start a new connection in Observe mode.
- Move to Standard only after semantic reads work.
- Enable Advanced only when intentionally delegating its configured authority.

## Configuration

```env
SCENEWORKS_MCP_ENABLED=true
SCENEWORKS_MCP_MODE=observe
SCENEWORKS_ADVANCED_SESSION_PERMISSIONS=["repository_read","repository_write","shell_execute","git_commit","network_access","subagents"]
SCENEWORKS_MCP_TOOL_MAX_CHARS=120000
```

`SCENEWORKS_MCP_ALLOW_ACTIONS` remains only as backward compatibility for the
earlier WP11 prototype: when true with `MCP_MODE=observe`, effective mode is
Standard. New deployments should use `MCP_MODE`.

The same non-secret settings are configurable from **Settings -> ChatGPT / MCP**.

## Typical reasoning loops

### Standard — substantial governed task

1. `get_project_context`.
2. `search_memory` as needed.
3. `inspect_repository` for missing code evidence.
4. Define engineering contract and `create_task`.
5. Start governed workflow.
6. Inspect task/diff/execution/reviewer evidence.
7. Revise/send back/accept deliberately.

### Advanced — ChatGPT master, Gemini execution subagent

1. `get_project_context`.
2. `agent_session.create` with the smallest capability subset needed.
3. `agent_session.prompt`: ask Gemini to investigate.
4. Poll `agent_session.get` and reason over the result.
5. Prompt again for targeted evidence/tests/implementation.
6. Inspect `agent_session.diff` after modifications.
7. Ask Gemini to verify or commit if appropriate.
8. Optionally create a normal SceneWorks review task/use the governed Reviewer
   for independent verification before integration.
9. Close the Advanced session.

## Non-goals

WP11 deliberately does not:

- make ChatGPT automatically intercept dashboard requests;
- expose raw host tools over MCP;
- provide an OS/container sandbox;
- make external model output authoritative memory;
- replace ACP with MCP;
- automatically merge Advanced/session/task branches;
- implement fully customizable roles (planned separately from WP11);
- create a dependency on CodexPro.

See `docs/tutorials/chatgpt-mcp-plugin.md` for connection/setup.
