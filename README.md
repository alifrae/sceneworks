# SceneWorks V2.5

SceneWorks is an **AI-native software company control plane**: a standalone web
application for operating software projects through virtual company roles
(CEO, CTO, Chief Architect, Product, Engineer, Reviewer/QA, GTM, Technical
Expert) while a human founder remains the final authority.

SceneWorks is independent from any managed product or repository. Managed
repositories never know SceneWorks exists: SceneWorks code is never added to
them, and agents only ever work in isolated Git worktrees outside the human
working tree.

```
Browser
   │  REST + SSE
   ▼
SceneWorks Web (Next.js 15, React 19)
   │  REST + SSE
   ▼
SceneWorks API (FastAPI) ──► SQLite (state, executions, events, checkpoints)
   │
   ▼
LangGraph WorkflowManager  ←── orchestrates Architect→Engineer→Reviewer
   │
   ▼
ExecutionEngine ──► AgentBackend
   │                   ├── Gemini ACP Backend   (Gemini CLI, ACP v1 over stdio)
   │                   ├── OpenHands Backend    (OpenHands Agent Server, HTTP/CLI)
   │                   └── Fake Backend         (scripted, tests/demos)
   │
   ▼
Git (isolated worktrees, commits, diffs)
```

## Architecture overview

SceneWorks defines the company and the workflow; roles define responsibilities
and permissions; an `AgentBackend` abstraction defines execution capability.
Tasks move through an explicit state machine (`NEW → ARCHITECTURE_ANALYSIS →
AWAITING_ARCHITECTURE_APPROVAL → READY_TO_IMPLEMENT → IMPLEMENTING →
TESTING → REVIEWING → CHANGES_REQUESTED → … → READY_FOR_HUMAN →
ACCEPTED/REJECTED`, plus FAILED/CANCELLED). Every agent invocation is a
persistent `Execution` that streams structured events; the UI shows them live
over Server-Sent Events. Nothing merges into the human branch automatically.

### Key components

| Piece | Location |
|---|---|
| Settings (env + DB overrides) | `backend/app/config/settings.py`, `backend/app/services/settings.py` |
| Domain models (SQLite, SQLAlchemy 2) | `backend/app/models.py` |
| Task state machine | `backend/app/domain/task_states.py` |
| Role permissions | `backend/app/domain/permissions.py` |
| Company roles (configuration) | `backend/app/roles/definitions.py` |
| Editable role prompts | `backend/app/roles/prompts/*.md` |
| Prompt building | `backend/app/roles/prompts.py` |
| Git worktree service | `backend/app/git/workspace.py` |
| AgentBackend protocol | `backend/app/agents/base.py` |
| Fake agent backend (tests/demos) | `backend/app/agents/fake.py` |
| Gemini ACP backend (ACP v1 client + proxy) | `backend/app/agents/gemini_acp.py` |
| **OpenHands backend (Agent Server, HTTP/CLI)** | `backend/app/agents/openhands.py` |
| Backend registry | `backend/app/agents/registry.py` |
| Execution engine (async, cancellation, recovery) | `backend/app/execution/engine.py` |
| Event bus + durable event store | `backend/app/events/bus.py`, `backend/app/events/store.py` |
| V2.2+ LangGraph WorkflowManager | `backend/app/workflows/manager.py` |
| V2.2+ LangGraph state definitions | `backend/app/workflows/state.py` |
| Workflow utilities (shared helpers) | `backend/app/services/workflow.py` |
| Company ask service | `backend/app/services/company.py` |
| API routes | `backend/app/api/` |
| Frontend (Next.js app router) | `web/app/`, `web/components/`, `web/lib/` |

## LangGraph workflow (V2.2+)

SceneWorks V2.2 replaced the linear workflow service with a **LangGraph
state graph** with durable checkpointing via `langgraph-checkpoint-sqlite`.

The graph includes:

| Node | Function |
|---|---|
| **triage** | Analyzes task and routes to architecture analysis or direct implementation |
| **architect** | Read-only architecture analysis in a detached worktree |
| **human_approval** | Interrupts for human approval (approve/reject/revision) |
| **engineer** | Implements changes in an isolated worktree with full write/shell access |
| **testing** | Runs project-configured test commands |
| **reviewer** | Inspects diff, commits, tests; emits `APPROVED` or `CHANGES_REQUESTED` |
| **complete** | Finalizes workflow, marks task `READY_FOR_HUMAN` |

### V2.2 key features

- **Advisory routing**: The triage node inspects the task and suggests
  Product/CTO/Technical Expert involvement before implementation when
  appropriate.
- **Auto-repair loop**: When the reviewer returns `CHANGES_REQUESTED`, the
  graph automatically routes back to the engineer node. This loops up to
  `max_review_iterations` (default 3) before requesting human intervention.
- **Idempotent workflow starts**: Duplicate `start_implementation` calls
  wait for the active graph to complete rather than spawning duplicates.
- **Worktree continuity**: Repair iterations reuse the same engineer worktree,
  preserving context across review cycles.
- **REST compatibility**: The workflow graph integrates with the existing
  REST API (`/api/tasks/{id}/actions/*`) without API changes.

## Agent backends

SceneWorks isolates execution providers behind the `AgentBackend` protocol:

```text
AgentBackend
├── FakeAgentBackend         (scripted, for tests/demos)
├── GeminiACPBackend         (Gemini CLI via ACP v1 over stdio)
└── OpenHandsBackend         (OpenHands Agent Server, HTTP/CLI)
```

### Gemini ACP backend

The first and default backend. Gemini CLI is launched in ACP mode (`--acp`,
protocol version 1 over stdio). SceneWorks mediates every file read/write
and command through the ACP client proxy, enforcing role permissions.

Configuration: `SCENEWORKS_GEMINI_EXECUTABLE`, `SCENEWORKS_GEMINI_MODEL`,
`SCENEWORKS_GEMINI_EXTRA_ARGS`.

### OpenHands backend (V2.3) — experimental in V2.5

[OpenHands](https://github.com/OpenHands/openhands) is an open-source AI
coding agent platform. The OpenHands backend supports three modes:

1. **SDK/WebSocket mode** (preferred): Connect via the official
   `openhands-sdk` package with WebSocket streaming.
   Set `SCENEWORKS_OPENHANDS_URL=http://localhost:8000`.
2. **HTTP polling mode** (compatibility fallback): Same REST API without SDK.
3. **CLI/headless mode** (development fallback only): Launch as subprocess.
   Set `SCENEWORKS_OPENHANDS_EXECUTABLE` or put `openhands` on PATH.

Configuration: `SCENEWORKS_OPENHANDS_URL`, `SCENEWORKS_OPENHANDS_EXECUTABLE`,
`SCENEWORKS_OPENHANDS_MODEL`, `SCENEWORKS_OPENHANDS_API_KEY`.

**Status: EXPERIMENTAL / UNVALIDATED.** No live integration test has been
performed against a running OpenHands Agent Server. The adapter reflects the
documented SDK API but has not been verified end-to-end. Gemini ACP is the
validated and default backend. See [docs/backends.md](docs/backends.md).

### Backend selection

Backends are selected by role configuration in `backend/app/roles/definitions.py`
or via the `SCENEWORKS_DEFAULT_BACKEND` setting. Each role may use a different
backend without workflow changes.

## Project Memory (V2.4)

SceneWorks V2.4 adds lightweight persistent project memory for capturing
architecture decisions, technology choices, product decisions, initiative
summaries, and constraints. Memory items support create, view, edit, archive,
supersede, and search/filter. Approved memories are injected into relevant
workflow nodes (Triage, Product, CTO, Technical Expert, Architect) as
bounded context. Engineer/Reviewer receive only relevant approved
decisions/constraints. Memory never converts speculative LLM output into
accepted project truth — extracted decisions remain proposals until
accepted.

## Git worktree safety

- Agents **never touch the human working tree**. All agent work happens in
  isolated Git worktrees under `SCENEWORKS_WORKTREE_ROOT`.
- The Architect uses a **detached read-only worktree** (no branch, no commits).
- The Engineer creates a **task branch** (`sw-task-<id>`) in an isolated
  worktree and commits there.
- The Reviewer uses a **disposable worktree** for inspection and validation.
- Worktrees are cleaned up automatically on task completion.
- Dirty (uncommitted) files in the human tree do not leak into worktrees.

## Setup

Requirements: Python 3.12+, Node.js >= 20, Git. [uv](https://docs.astral.sh/uv/) is the Python dependency manager.

### Backend

```bash
cd backend
uv sync
copy ..\\.env.example .\\.env    # Optional; or set SCENEWORKS_* env vars
uv run uvicorn app.main:app --reload    # http://127.0.0.1:8010 (docs at /docs)
```

### Frontend

```bash
cd web
npm install
npm run dev                       # http://localhost:3000
```

The frontend calls the API at `http://127.0.0.1:8010` (override with
`NEXT_PUBLIC_API_URL`). CORS is configured for `http://localhost:3000`.

## Registering a repository

Projects page → **Add existing repository** → enter the absolute path of a
local Git repository. SceneWorks validates that the path exists and is a Git
repository, captures the current head branch, and never touches its files.

Configure `test_commands` (one per line) and optional context file paths
(e.g. `docs/architecture.md`); the default context files
`AGENTS.md`, `ARCHITECTURE.md`, `CONTRIBUTING.md`, `ROADMAP.md` are read
automatically if present at the repository root.

## Executing a task (end-to-end)

1. **Tasks → New task** on a registered project.
2. **Start architecture analysis** — the Architect (read-only, in a detached
   worktree) inspects the repository and task and produces a structured
   analysis. Task enters `AWAITING_ARCHITECTURE_APPROVAL`.
3. **Approve** (or reject / request revision / amend) the architecture.
4. **Approve architecture** — the LangGraph workflow auto-runs Engineer →
   (optional testing) → Reviewer. The Engineer creates a task branch
   (`sw-task-<id>`), edits files, runs commands, and commits. If the Reviewer
   requests changes, the Engineer is automatically re-invoked (repair loop).
5. **Review result** — task reaches `READY_FOR_HUMAN` with implementation
   summary, diff, and review verdict.
6. **Accept** or **reject** — SceneWorks never merges automatically.

The task detail page streams activity live, shows architecture analysis,
implementation summary, review result, the complete diff, and offers
applicable actions for the current state.

## Company roles

| Role | Key | Permissions | Invocation |
|---|---|---|---|
| CEO | `ceo` | read, network | Manual ask |
| CTO | `cto` | read, network | Manual ask |
| Chief Architect | `architect` | read-only | Workflow (architecture phase) |
| Product | `product` | read, network | Manual ask |
| Engineer | `engineer` | read, write, shell, git | Workflow (implementation) |
| Reviewer / QA | `reviewer` | read, shell | Workflow (review) |
| Technical Expert | `technical_expert` | read, shell | Manual ask |
| GTM | `gtm` | read, network | Manual ask |

Roles are configuration — purpose, permissions, backend, model profile —
not running processes. Manual roles (CEO, CTO, Product, GTM, Technical Expert)
are invoked via "Ask" and their outputs are stored as company artifacts. They
never modify code and never trigger chains of other agents.

## Testing

```bash
# Backend tests (no live model required)
cd backend
uv sync
uv run pytest                # 105 tests

# Frontend lint and build
cd web
npm run build
npm run lint

# Browser E2E tests (requires running backend + frontend)
npm run test:e2e
```

Test categories:

- **Unit tests** (`test_domain.py`): State machine, permissions, roles,
  prompt limits.
- **Git integration tests** (`test_git_workspace.py`): Worktree creation,
  branches, commits, diffs, cleanup, isolation.
- **Engine tests** (`test_engine.py`): Execution lifecycle, events,
  cancellation, restart recovery (scripted FakeAgentBackend).
- **API tests** (`test_api.py`): Projects, tasks, invalid transitions,
  full workflow, changes-requested loop, company asks, dashboard.
- **ACP protocol tests** (`test_gemini_acp.py`): Mock ACP v1 server,
  prompting, event mapping, fs-write denial for read-only roles,
  cancellation.
- **Workflow graph tests** (`test_workflow_graph.py`): LangGraph topology,
  persistence, approval/rejection/revision, auto-repair, repair limits,
  idempotency, worktree continuity, cancellation.
- **OpenHands tests** (`test_openhands.py`): Contract validation,
  health checks, configuration, registry integration, experimental label.
- **Memory tests** (`test_memory.py`): CRUD, search, archival,
  injection context, provenance.
- **Browser E2E tests** (`web/e2e/`): Playwright-based UI → API →
  workflow flow. Uses FakeAgentBackend. No live models.
- **Live smoke test** (optional): Run the app with a real Gemini project
  and execute a task as described above.

## Documentation

- [Quick Start](docs/quickstart.md) — First 10 minutes guide from zero to
  running SceneWorks.
- [Architecture](docs/architecture.md) — Component responsibilities,
  dependency boundaries, task lifecycle, event flow.
- [Web UI Tutorial](docs/tutorials/web-ui.md) — Step-by-step guide from
  installation to accepting a task result.
- [Adding a Backend](docs/backends.md) — Guide for implementing new
  AgentBackend providers.
- [Development](docs/development.md) — Developer setup and conventions.
- [Known Limitations](docs/limitations.md) — Current boundaries and
  non-goals.

## V2.5 changes

V2.5 is the final V2 release — closing gaps, hardening reliability, and
adding browser E2E tests.

- **Dashboard race fixed**: Dashboard queries use consistent transaction
  snapshots, preventing stale KPI data.
- **OpenHands SDK audit**: Backend rewritten to use the documented
  `openhands-sdk` API (`Workspace`, `Conversation`, `LLM`, `Agent`).
  Marked experimental/unvalidated until live tested.
- **6 reliability bugs fixed**: `retry` from FAILED state (critical);
  cancellation now terminates asyncio graph tasks; SSE event deduplication
  fixed; workflow events stream live; execution-wait race resolved;
  `parse_review_verdict` no longer auto-approves empty output.
- **Recovery improvements**: `recover_interrupted` emits transition events
  and updates timestamps; advisory role worktrees auto-cleaned up.
- **uv cleanup**: dev deps moved to `[dependency-groups]`; OpenHands
  optional dep narrowed to `openhands-sdk`; lock file pruned of 200+
  transitive packages.
- **Documentation residue removed**: no more pip/manual venv references.
- **UI enhancements**: Workflow progress stepper with per-phase status
  (done/active/waiting); backend health indicators on dashboard; failed
  execution table with error details; human-waiting state highlighted.
- **Browser E2E tests**: Playwright-based tests for the full UI→API→
  workflow path using FakeAgentBackend.
- **Evaluation framework**: Repeatable scenario-based evaluation for bug
  fixes, features, refactors, architecture decisions, and more.

## Security and trust assumptions

- The API binds to localhost by default. It is a trusted control plane, not a
  public service — there is no user authentication. Do not expose it beyond
  localhost.
- Agents run on the same machine as the API, inside isolated Git worktrees.
  File access and commands are mediated by backends (ACP proxy for Gemini,
  workspace confinement for OpenHands), enforcing role permissions and path
  boundaries.
- Commands run with the SceneWorks process's user permissions. This is not an
  OS-level sandbox; treat the worker machine as trusted.
- Agent output is always reviewable (events, logs, diffs) and nothing is
  merged automatically.
- Secrets never live in the frontend or in SceneWorks settings; backends
  use their own authentication mechanisms.
- SceneWorks remains independent of managed repositories — no SceneWorks code
  is added to managed repos.

## Current limitations

See [docs/limitations.md](docs/limitations.md) for details.

- Single-machine deployment: API + worker + SQLite run together.
- No user accounts/teams, no GitHub/PR integration, no automatic merging.
- No RAG/embeddings — memory is SQLite-backed text retrieval with metadata/tags.
- ACP backends run sequentially at present.
- OpenHands HTTP mode requires a separately running OpenHands Agent Server.
