# WP11 — MCP Reasoning Interface

## Status

Implemented on `wp11-mcp-reasoning-interface`.

WP11 makes SceneWorks directly usable by external reasoning clients such as
ChatGPT while keeping repository execution inside SceneWorks' existing agent,
permission, worktree, workflow and provenance boundaries.

The governing rule is:

> **MCP exposes SceneWorks capabilities, not the underlying machine.**

There are intentionally no MCP tools named `read_file`, `write_file`, `shell`,
`git_commit`, `git_push`, `sql`, or equivalent machine primitives.

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
                 ┌─────────────────────┼─────────────────────┐
                 │                     │                     │
             project/task          project memory        evidence/events
                 state             + provenance          + Git metadata
                 │                     │                     │
                 └─────────────────────┼─────────────────────┘
                                       │
                               governed workflow
                                       │
                                  AgentBackend
                                       │
                                      ACP
                                       │
                                  Gemini CLI
                                       │
                       commit-pinned isolated worktrees
                              files / shell / tests / Git
```

### Responsibility split

| Component | Primary responsibility |
|---|---|
| Human | Intent, approval, final acceptance, authority over project truth |
| ChatGPT / external MCP client | Problem framing, strategic reasoning, cross-evidence synthesis, independent review, task creation/control when explicitly enabled |
| SceneWorks | Source of truth, task lifecycle, project memory, permissions, routing, worktrees, execution history, provenance and audit |
| Gemini CLI over ACP | Repository exploration, shell/tool execution, implementation and verification inside the role's bounded workspace |
| Reviewer role | Independent implementation review before human acceptance |

ChatGPT is therefore an **optional higher-level reasoning/supervision layer**, not
a mandatory dependency for SceneWorks. A dashboard-created task still works when
ChatGPT is absent.

## Two operating modes

### Dashboard/autonomous mode

```text
SceneWorks dashboard -> SceneWorks workflow -> Gemini CLI/ACP
```

ChatGPT does not silently intervene. SceneWorks remains autonomous and usable
without an external reasoning client.

### MCP-supervised mode

```text
human -> ChatGPT -> MCP -> SceneWorks -> ACP -> Gemini CLI
                       ^                    |
                       └---- evidence ------┘
```

The reasoning client can inspect project state, invoke a read-only technical
inspection, create a structured task, let SceneWorks execute it, then inspect
execution results/diffs/reviewer evidence without the user manually copying
context between systems.

## MCP endpoint and protocol

SceneWorks exposes one stateless endpoint on the existing FastAPI service:

```text
POST /mcp
```

`GET /mcp` returns setup/health metadata and no project data.

The endpoint supports:

- MCP `2026-07-28` stateless `server/discover`, `tools/list`, `tools/call`;
- legacy `initialize` negotiation for `2025-11-25` / compatible clients;
- tool annotations for read-only vs action/destructive semantics;
- bounded responses for large diffs/artifacts;
- legacy clients that omit the modern MCP routing headers.

No additional MCP SDK dependency was added. The transport uses the existing
FastAPI/Pydantic stack so `uv sync --frozen` remains valid.

## Tool surface

### Read-only reasoning tools

| Tool | Purpose |
|---|---|
| `sceneworks.capabilities` | Discover role/backend permissions, MCP policy and the hands/eyes execution model |
| `sceneworks.list_projects` | List projects and active task counts |
| `sceneworks.get_project_context` | Project policy, configured verification commands, current Git snapshot, recent tasks and accepted memory |
| `sceneworks.list_tasks` | Find tasks by project/status/role |
| `sceneworks.get_task` | Contract, architecture, implementation/review output, executions, events, provenance and legal next actions |
| `sceneworks.get_task_diff` | Git-observed implementation diff/commits/status while the task worktree exists |
| `sceneworks.get_execution` | Exact role/backend/model/status/result and execution events without leaking host paths |
| `sceneworks.search_memory` | Search memory with `accepted` as the safe default authority level |
| `sceneworks.list_artifacts` | Retrieve stored role/execution artifacts |

### Action tools

Action tools are disabled by default. They become available operationally only
when:

```text
SCENEWORKS_MCP_ALLOW_ACTIONS=true
```

| Tool | Purpose |
|---|---|
| `sceneworks.inspect_repository` | Starts Technical Expert on a detached commit-pinned snapshot; this is the external reasoner's **eyes** |
| `sceneworks.ask_role` | Ask an advisory role on an exact project snapshot |
| `sceneworks.create_task` | Create a structured task/engineering contract; does not start work implicitly |
| `sceneworks.task_action` | Apply the same governed task actions available to the dashboard/API |

`task_action` does not grant raw code-writing authority to the MCP client. Starting
implementation enters the normal SceneWorks workflow. The Engineer then obtains
its own isolated branch worktree and the ACP permissions defined for that role.

## Gemini CLI as hands and eyes

WP11 does **not** add a second coding harness. It relies on the ACP boundary
SceneWorks already owns and qualifies it for the MCP-supervised workflow.

### Eyes

`sceneworks.inspect_repository` maps to the `technical_expert` role:

- commit-pinned detached worktree;
- repository read permission;
- shell execution for inspection/testing commands;
- no repository write permission;
- no Git commit permission.

The MCP client receives an execution id and polls `sceneworks.get_execution` for
the result. This lets ChatGPT request additional repository evidence without
receiving arbitrary host filesystem access.

### Hands

Implementation remains the `engineer` role:

- isolated branch worktree;
- repository read/write;
- shell execution;
- Git commit capability;
- SceneWorks captures the resulting commit/diff/provenance;
- Reviewer runs before human acceptance.

The ACP adapter continues to enforce worktree confinement and role permission
checks for the mediated filesystem/terminal requests it receives. Gemini CLI
built-in operations that ACP only reports after the fact retain the limitations
documented in `gemini-capability-matrix.md`; WP11 does not pretend MCP fixes that
lower-level sandbox limitation.

## Adaptive role routing

Using every role on every task adds latency without necessarily adding
information. WP11 therefore adds one conservative deterministic shortcut.

A task may skip Architect only when all of these are true:

1. Triage classifies it as a `bug`.
2. It requires implementation.
3. Priority is not `high`.
4. The engineering contract contains a non-empty `allowed_scope`.
5. It contains non-empty `required_tests`.
6. It contains non-empty `acceptance_criteria`.

The transition is explicit and audited:

```text
ARCHITECTURE_ANALYSIS --skip_architecture--> READY_TO_IMPLEMENT
```

Then the graph continues:

```text
Triage -> optional advisers -> Engineer -> Reviewer -> READY_FOR_HUMAN
```

For an unbounded/risky implementation, an LLM-generated `use_architect=false`
decision is **overridden** and the full architecture path is retained. The LLM
cannot waive the deterministic safety gate.

Product, CTO and Technical Expert remain advisory and are invoked only when
triage selects them. Cross-cutting features/refactors/high-risk work therefore
keep the richer reasoning workflow while bounded bugs avoid a redundant
Architect round-trip.

## Memory and evidence authority

WP11 preserves the existing knowledge firewall:

- `accepted` project memory is authoritative project knowledge;
- `proposed` memory is visible but not silently injected as truth;
- Gemini/role output is execution evidence/inference, not authoritative memory;
- Git remains authoritative for what changed;
- task results carry commit/worktree provenance;
- human acceptance remains explicit.

The MCP tools report these distinctions so an external model does not have to
infer authority from prose.

## Security model

The SceneWorks FastAPI service is still a single-user local control plane and
does not implement user authentication or OAuth.

Therefore:

- keep the service bound to `127.0.0.1` by default;
- prefer ChatGPT's Secure MCP Tunnel for a local/private instance;
- never expose `http://<machine>:8010` directly to the public internet;
- if using a remote Server URL, terminate TLS and authentication in trusted
  infrastructure in front of SceneWorks;
- leave `SCENEWORKS_MCP_ALLOW_ACTIONS=false` until the connection is trusted;
- connecting a read-only MCP client must not implicitly enable actions;
- large tool responses are bounded by `SCENEWORKS_MCP_TOOL_MAX_CHARS`.

This application-level boundary does not replace OS/container sandboxing for
Gemini subprocesses. See `docs/gemini-capability-matrix.md` and
`docs/limitations.md`.

## Configuration

```env
SCENEWORKS_MCP_ENABLED=true
SCENEWORKS_MCP_ALLOW_ACTIONS=false
SCENEWORKS_MCP_TOOL_MAX_CHARS=120000
```

Restart the backend after changing environment settings.

## Suggested MCP reasoning loop

For a substantial task:

1. `get_project_context` with a focused query.
2. `search_memory` if additional accepted decisions are needed.
3. `inspect_repository` when code-level evidence is missing.
4. Poll `get_execution` for the Technical Expert result.
5. Define a bounded engineering contract.
6. `create_task`.
7. `task_action(start-architecture)`; bounded bugs may automatically skip the
   Architect, while riskier work enters architecture approval.
8. Inspect `get_task`, `get_task_diff`, execution history and review evidence.
9. Use `send-back`, revision, reject or accept deliberately.

The human can interrupt or continue from the SceneWorks dashboard at any point
because MCP and the dashboard operate on the same persisted task/workflow state.

## Non-goals

WP11 deliberately does not:

- make ChatGPT a background service that automatically intercepts dashboard
  requests;
- route raw shell/filesystem/Git access through MCP;
- let external model output become accepted project memory automatically;
- replace ACP with MCP;
- make Gemini CLI optional for the configured `gemini_acp` backend;
- automatically merge agent branches into a human branch;
- create a dependency on CodexPro or another ChatGPT coding bridge.

See `docs/tutorials/chatgpt-mcp-plugin.md` for the connection procedure.
