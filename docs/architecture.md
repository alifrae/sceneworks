# SceneWorks Architecture

## Overview

SceneWorks is an AI-native software company control plane that orchestrates
virtual company roles through a LangGraph workflow to produce and review code
changes in isolated Git worktrees.

```mermaid
graph TD
    UI["Web UI (Next.js 15)"]
    API["FastAPI REST + SSE"]
    WM["LangGraph WorkflowManager"]
    EE["ExecutionEngine"]
    AB["AgentBackend"]
    GEM["Gemini ACP"]
    OH["OpenHands"]
    GIT["Git Worktrees"]

    UI -->|REST / SSE| API
    API --> WM
    WM --> EE
    EE --> AB
    AB --> GEM
    AB --> OH
    AB --> GIT
    EE --> GIT
```

## Component responsibilities

### FastAPI application (`backend/app/main.py`)

- HTTP API server bound to `127.0.0.1:8010`
- Registers routers: projects, tasks, executions, events, company, settings,
  dashboard
- Serves SSE streams for live execution events
- CORS configured for Next.js dev server (`localhost:3000`)

### LangGraph WorkflowManager (`backend/app/workflows/manager.py`)

- LangGraph is the **sole orchestration mechanism** for task workflows
- Nodes are thin adapters that delegate real work to existing services
- Durable checkpointing via `langgraph-checkpoint-sqlite`
- Thread prefix: `task-<task_id>`, enabling checkpoint resume across restarts
- Key nodes: triage, architect, human_approval, engineer, testing, reviewer,
  complete

### ExecutionEngine (`backend/app/execution/engine.py`)

- Runs agent executions as asyncio tasks (non-blocking)
- Persists lifecycle events to SQLite via EventStore
- Supports cancellation with configurable grace period
- Recovers interrupted executions on startup
- Generic: does not know about tasks, roles, or prompts
- Calls `on_execution_finished` hook for workflow continuation

### AgentBackend protocol (`backend/app/agents/base.py`)

- Isolates execution providers behind a stable contract: `run()`, `cancel()`,
  `health()`
- The `AgentEventSink` abstraction channels per-execution events to
  SceneWorks event storage and SSE streaming
- `Workspace` and `AgentRequest` are backend-agnostic data types
- Backends must emit only the generic event vocabulary from
  `app/events/types.py`

### Gemini ACP backend (`backend/app/agents/gemini_acp.py`)

- Launches Gemini CLI in ACP mode (protocol v1 over stdio)
- Implements the client side of ACP: sends `initialize`, `session/new`,
  `session/prompt` requests
- Implements the server side of ACP (`AgentPolicy`): mediates every
  `fs/read_text_file`, `fs/write_text_file`, `terminal/create` request
  according to role permissions
- Enforces workspace boundaries (paths must resolve within worktree or repo)
- Maps ACP `session/update` notifications to SceneWorks event vocabulary

### OpenHands backend (`backend/app/agents/openhands.py`)

- Supports HTTP mode (connect to running OpenHands Agent Server via REST or
  WebSocket/SDK) and CLI mode (launch OpenHands as subprocess — development
  fallback only)
- Maps OpenHands conversation lifecycle to SceneWorks event vocabulary
- Enforces workspace confinement (passes exact worktree path to server)
- Configuration: `SCENEWORKS_OPENHANDS_URL`,
  `SCENEWORKS_OPENHANDS_EXECUTABLE`, `SCENEWORKS_OPENHANDS_MODEL`

### Git worktree service (`backend/app/git/workspace.py`)

- Creates detached (read-only) and branch (writable) worktrees
- Resolves base commits, creates task branches (`sw-task-<id>`)
- Manages commits, diffs, and cleanup
- Ensures worktrees are created outside the human working tree
- Dirty human-tree files do not leak into worktrees

### Event system (`backend/app/events/`)

- `EventBus`: In-process publish/subscribe for SSE streaming
- `EventStore`: Durable SQLite-backed event persistence
- `types.py`: Structured event vocabulary with human-readable labels
- V2.2 events: workflow triage, role selection, repair started,
  repair limit reached

### Domain layer (`backend/app/domain/`)

- `task_states.py`: Explicit state machine with transition validation
- `permissions.py`: Role permissions (read, write, shell, git, network)

### Role system (`backend/app/roles/`)

- `definitions.py`: Role definitions as frozen dataclasses — key, permissions,
  backend, model profile, responsibilities
- `prompts.py` + `prompts/*.md`: Prompt building from editable markdown
  templates
- `registry.py`: Role lookup with backend resolution

### Project Memory (`backend/app/services/memory.py`) — V2.4

- Lightweight persistent project memory backed by SQLite text/metadata
  retrieval
- Types: initiative_summary, architecture_decision, product_decision,
  technology_decision, constraint
- Statuses: proposed, accepted, archived, superseded
- Provenance tracking: source, source_task_id, source_execution_id
- Deterministric retrieval via `memory.get_relevant()` for workflow injection
- Bounded context injected into Triage, Product, CTO, Technical Expert,
  and Architect nodes
- Memory never converts speculative LLM output into accepted project truth

### Frontend (`web/`)

- Next.js 15 App Router, React 19, TypeScript
- Pages: Dashboard, Projects, Tasks (detail with live streaming),
  Executions, Company, Settings
- Components: ActionBar, DiffView, EventLog, Markdown, Sidebar, StatusBadge

## Dependency boundaries

```
Web UI (Next.js)
  │  depends on
  ▼
FastAPI REST + SSE
  │  depends on
  ▼
LangGraph WorkflowManager  ─── depends on ──► ExecutionEngine
  │  depends on                                  │  depends on
  ▼                                              ▼
Workflow/Task services           AgentBackend protocol
  │  depends on                    │  implements
  ▼                               ▼
Domain models (SQLAlchemy)    Gemini ACP / OpenHands / Fake
  │
  ▼
SQLite (state, events, checkpoints)

Agents, backends, worktree services, and event system
must NOT depend on LangGraph.
LangGraph is an orchestration dependency only.
```

## Task/initiative lifecycle

```mermaid
stateDiagram-v2
    [*] --> NEW
    NEW --> ARCHITECTURE_ANALYSIS: start_architecture
    ARCHITECTURE_ANALYSIS --> AWAITING_ARCHITECTURE_APPROVAL: architecture_completed
    AWAITING_ARCHITECTURE_APPROVAL --> READY_TO_IMPLEMENT: approve_architecture
    AWAITING_ARCHITECTURE_APPROVAL --> ARCHITECTURE_ANALYSIS: request_architecture_revision
    AWAITING_ARCHITECTURE_APPROVAL --> REJECTED: reject_task
    READY_TO_IMPLEMENT --> IMPLEMENTING: start_implementation
    IMPLEMENTING --> TESTING: implementation_completed
    TESTING --> REVIEWING: start_review
    REVIEWING --> READY_FOR_HUMAN: review_completed (APPROVED)
    REVIEWING --> CHANGES_REQUESTED: review_changes_requested
    CHANGES_REQUESTED --> IMPLEMENTING: start_implementation (auto-repair)
    READY_FOR_HUMAN --> CHANGES_REQUESTED: send_back_to_engineer
    READY_FOR_HUMAN --> ACCEPTED: accept
    READY_FOR_HUMAN --> REJECTED: reject
    FAILED --> READY_TO_IMPLEMENT: retry
    ARCHITECTURE_ANALYSIS --> CANCELLED: cancel
    IMPLEMENTING --> CANCELLED: cancel
    REVIEWING --> CANCELLED: cancel
```

## Engineer ↔ Reviewer repair loop (V2.2+)

When the reviewer emits `VERDICT: CHANGES_REQUESTED`, the LangGraph graph
automatically routes back to the engineer node. The loop:

1. Reviewer inspects engineer's commit/diff/tests
2. If `CHANGES_REQUESTED`, graph routes to engineer
3. Engineer works in the **same worktree** (continuity), applies fixes
4. Cycle repeats until `APPROVED` or `max_review_iterations` exhausted
5. If limit reached, task stays in `CHANGES_REQUESTED` for human intervention

The `max_review_iterations` setting (default 3) is configurable via
`SCENEWORKS_MAX_REVIEW_ITERATIONS`.

## Advisory routing (V2.2+)

The triage node inspects each task and may suggest advisory input from
Product, CTO, or Technical Expert roles before implementation. This is
advisory only — tasks can still progress without advisory input unless
the role specifies `approval_authority`.

## Cancellation and recovery

- **Cancellation**: API call → engine signals `AgentEventSink.cancel()` →
  backend cancels process/session → engine finalizes as `CANCELLED`
- **Grace period**: `SCENEWORKS_CANCEL_GRACE_SECONDS` (default 15s) before
  forced task cancellation
- **Recovery**: On startup, the engine marks all `QUEUED`/`STARTING`/`RUNNING`
  executions as `INTERRUPTED` and corresponding tasks as `FAILED`
- **LangGraph checkpoints**: Workflow state is durably checkpointed in
  SQLite; interrupted workflows can resume from their last checkpoint

## Persistence

| Store | Backend | Content |
|---|---|---|
| SQLite (sceneworks.db) | SQLAlchemy 2 + aiosqlite | Projects, tasks, executions, events, artifacts |
| SQLite (workflow_checkpoints.db) | langgraph-checkpoint-sqlite | LangGraph state checkpoints |
| Filesystem | worktree_root | Isolated Git worktrees |

## Event flow

```mermaid
sequenceDiagram
    participant UI as Web UI
    participant API as FastAPI
    participant WM as WorkflowManager
    participant EE as ExecutionEngine
    participant AB as AgentBackend
    participant EV as EventBus/Store

    UI->>API: POST /api/tasks/{id}/actions/start-architecture
    API->>WM: start_workflow(task_id)
    WM->>EE: Create execution + start
    EE->>EV: emit(EXECUTION_STARTED)
    EE->>AB: run(request, workspace, sink)
    AB->>EV: emit(agent.text_delta, tool.started, ...)
    AB-->>EE: AgentResult(completed)
    EE->>EV: emit(EXECUTION_COMPLETED)
    EE->>WM: on_execution_finished(execution_id)
    WM->>WM: LangGraph continues to next node
    EV-->>UI: SSE stream (live updates)
    UI->>API: GET /api/tasks/{id}/events (on connect)
```

## Permissions

| Permission | Architect | Engineer | Reviewer | CTO/CEO/Product/GTM |
|---|---|---|---|---|
| `repository_read` | yes | yes | yes | yes |
| `repository_write` | **no** | yes | **no** | **no** |
| `shell_execute` | **no** | yes | yes | **no** |
| `git_commit` | **no** | yes | **no** | **no** |
| `network_access` | yes | **no** | **no** | yes |

Permissions are enforced by the backend adapter:
- **Gemini ACP**: The `AgentPolicy` rejects unauthorized `fs/write_text_file`
  and `terminal/create` requests from the agent
- **OpenHands**: Workspace confinement and configuration determine access
- **Prompt-level**: Role prompts instruct the agent of its limitations

## Repository / worktree model

```
~/projects/my-app/              ← human working tree (never touched)
    .git/
    src/
    README.md

~/sceneworks-worktrees/         ← SCENEWORKS_WORKTREE_ROOT
    my-app/                     ← bare repo clone
    my-app-sw-task-42/          ← engineer worktree (branch sw-task-42)
        src/
        README.md
        new-feature.py          ← agent changes here
    my-app-detached-7/          ← architect worktree (detached HEAD)
```

- Architect gets a **detached HEAD** worktree (no branch, no commits)
- Engineer gets a **branch worktree** (`sw-task-<id>`) with full write access
- Reviewer gets a **disposable worktree** for inspection
- Worktrees are cleaned up on task completion
- The human tree never contains agent changes

## Restart / cancellation behavior

- **Graceful shutdown**: `AppContext.shutdown()` cancels all active
  executions, marks them `INTERRUPTED`, and shuts down the workflow manager
- **Cancellation**: `ExecutionEngine.cancel()` signals the backend, then
  force-cancels the asyncio task after the grace period
- **LangGraph resume**: Checkpointed graphs can resume from where they left
  off after a restart
