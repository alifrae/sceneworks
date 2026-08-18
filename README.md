# SceneWorks V3.0

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

The nodes registered in `backend/app/workflows/manager.py`:

| Node | Function |
|---|---|
| **route_entry** | Entry point; routes by current task status to triage, engineer or reviewer |
| **triage** | Pins the workflow's base commit, classifies the request, selects advisory roles |
| **advisor_router** | Dispatches to each selected advisory role in turn, then to the architect |
| **product** / **cto** / **technical_expert** | Optional advisory analyses, each in its own detached worktree |
| **architect** | Read-only architecture analysis in a detached worktree |
| **architecture_approval** | LangGraph `interrupt()` — waits for human approve/reject/revision |
| **engineer** | Implements changes in an isolated branch worktree with write/shell access |
| **reviewer** | Inspects diff, commits and tests; emits `APPROVED` or `CHANGES_REQUESTED` |

There is no separate `testing` or `complete` node: the Engineer runs the
project's tests itself inside its worktree, and terminal states are reached by
the routing edges (`READY_FOR_HUMAN` when the reviewer approves, or when an
advisory-only task finishes its analysis).

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
protocol version 1 over stdio) with its working directory set to a
commit-pinned worktree.

**What the proxy actually enforces.** SceneWorks answers the agent's
client-side requests and applies role permissions there:

- `fs/read_text_file` / `fs/write_text_file` are confined to the pinned
  worktree, and writes additionally require `repository_write`. The main
  repository checkout is never an allowed path.
- `session/request_permission` is answered against the role: `execute` tool
  calls are refused unless the role has `shell_execute`. In live runs this
  demonstrably refused `git reflog`, `git remote -v` and
  `python -m pytest --version` for read-only roles.
- `terminal/create` requires `shell_execute`, and its working directory is
  validated against the worktree.

**What it does not enforce.** The agent runtime also has its own tools and
does not route everything through the client. Measured over the V3.0 live
runs, 28 of 191 observed tool calls arrived at the permission gate; the rest
were reported as `session/update` notifications after the fact. And a shell
command, once permitted, is a real process — it can reach anything the OS
lets the SceneWorks user reach. Treat the proxy as a strong guard rail and an
audit trail, **not as a sandbox against a hostile agent**.

The full Gemini ACP capability matrix is documented at
[docs/gemini-capability-matrix.md](docs/gemini-capability-matrix.md).

Configuration: `SCENEWORKS_GEMINI_EXECUTABLE`, `SCENEWORKS_GEMINI_MODEL`,
`SCENEWORKS_GEMINI_EXTRA_ARGS`.

### OpenHands backend — experimental, opt-in

[OpenHands](https://github.com/OpenHands/OpenHands) is an open-source AI coding
agent platform. SceneWorks can drive it through `openhands-sdk`.

```bash
cd backend && uv sync --extra openhands
SCENEWORKS_OPENHANDS_MODEL=lm_studio/google/gemma-4-e2b
SCENEWORKS_OPENHANDS_BASE_URL=http://127.0.0.1:1234/v1
```

Four modes, resolved explicitly and reported on every run: `local` (in-process,
no server), `remote` (Agent Server), `http` (REST polling), `cli` (subprocess).

**Status: EXPERIMENTAL.** WP2.5 performed a real live validation — see
[docs/wp2.5-openhands-validation.md](docs/wp2.5-openhands-validation.md).
What that established:

- `local` mode **executes real work** and is validated for **read-only roles**
  on openhands-sdk 1.17.0 + openhands-tools 1.17.0, Windows 11, with an
  OpenAI-compatible LLM endpoint. A qualification scenario passes against the
  real backend.
- **The Engineer cannot run on Windows**: the OpenHands V1 terminal tool raises
  `NotImplementedError` there, so shell-using roles are refused up front.
- `remote`, `http` and `cli` modes are implemented but **not validated**.
- 14 adapter defects were found and fixed, including that SDK mode could never
  emit any event or summary, cancellation was decorative, and the synchronous SDK
  was blocking the API event loop.

**Gemini ACP remains the default for every role** and is the only backend able to
run the Engineer on this platform. See [docs/backends.md](docs/backends.md).

### Backend selection

Backends are selected by role configuration in `backend/app/roles/definitions.py`
or via the `SCENEWORKS_DEFAULT_BACKEND` setting. Each role may use a different
backend without workflow changes.

## Project Memory

Persistent project memory for architecture decisions, technology choices,
product decisions, initiative summaries and constraints. Memory items support
create, view, edit, accept, reject, archive, supersede and search.

Retrieval is **deterministic and explainable**: term-based scoring over title,
content and tags, with every result carrying the reason it was selected
(`matched_terms`, `matched_tags`, `score`, `coverage`, per-signal contributions).
No embeddings, no vector database, no knowledge graph.

Only **accepted** memories are injected as authoritative context. Proposals are
returned separately for display and never reach the agent as settled project
truth; `propose_from_execution()` has no `status` parameter, so no caller can
promote speculation by passing an argument. Accepting is an explicit human
action, recorded in the event log with the actor and the previous state.

> **V2.4 → V3 correction.** V2.4 passed the *entire task description* into a
> single SQL `ILIKE` pattern. No stored memory contains a task description
> verbatim, so realistic descriptions retrieved **nothing** — measured at 0
> results where the same store returned 2 for an empty query. Project Memory was
> inert on the only path that mattered. See
> [docs/wp0-baseline-audit.md](docs/wp0-baseline-audit.md) F4 for the evidence and
> [docs/memory.md](docs/memory.md) for the current behaviour.

## Project Policy

A project's engineering contract — protected paths, architecture invariants,
forbidden dependency directions, documentation and performance requirements,
required review checks, go/no-go commands, release requirements — as a
structured contract distinct from free-text background reading.

`protected_paths` is checked **deterministically**: SceneWorks matches the
Engineer's actual diff against declared patterns itself
(`fnmatch.fnmatchcase`, case-sensitive and platform-independent), rather than
asking an LLM Reviewer to remember a list of globs while also reading a large
diff. Every other category remains judged by the Reviewer, but is handed to it
as an explicit, labelled contract rather than mixed into general context — and
the Reviewer alone gets the instruction that it is the enforcement point,
since **the Engineer must not be responsible for defining the criteria used to
approve its own work**.

Reaches every role consistently — Triage, Product, CTO, Technical Expert,
Architect, Engineer, Reviewer, and manual company asks — verified directly
against the actual rendered prompt text for each, not just designed to.

```bash
curl -X PUT http://127.0.0.1:8010/api/projects/1/policy \
  -H "Content-Type: application/json" \
  -d '{"protected_paths": ["generated/*", "api/public/*"]}'
```

See [docs/project-policy.md](docs/project-policy.md) for the full contract,
including a worked PCS-shaped example (documentation only — no PCS-specific
code exists in SceneWorks) and the qualification scenario proving a violation
is detected during review even when the reviewer itself approves.

## Git worktree safety

- Agents **never touch the human working tree**. All agent work happens in
  isolated Git worktrees under `SCENEWORKS_WORKTREE_ROOT`.
- The Architect uses a **detached read-only worktree** (no branch, no commits).
- The Engineer creates a **task branch** (`sw-task-<id>`) in an isolated
  worktree and commits there.
- The Reviewer uses a **disposable worktree** for inspection and validation.
- Worktrees are cleaned up automatically on task completion.
- Dirty (uncommitted) files in the human tree do not leak into worktrees.
- **fsmonitor daemons are suppressed** per-process: SceneWorks git operations
  and agent terminal commands set `GIT_CONFIG_PARAMETERS` to disable
  `core.fsmonitor`, preventing ~308 orphaned `git fsmonitor--daemon` processes
  observed during PCS worktree cycles. The user's global and repository-local
  `core.fsmonitor` configuration is never modified.

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
| CTO | `cto` | read, network | Workflow (triage) + Manual ask |
| Chief Architect | `architect` | read-only | Workflow (architecture phase) |
| Product | `product` | read, network | Workflow (triage) + Manual ask |
| Engineer | `engineer` | read, write, shell, git | Workflow (implementation) |
| Reviewer / QA | `reviewer` | read, shell | Workflow (review) |
| Technical Expert | `technical_expert` | read, shell | Workflow (triage) + Manual ask |
| GTM | `gtm` | read, network | Manual ask |

Roles are configuration — purpose, permissions, backend, model profile —
not running processes. Product, CTO, and Technical Expert participate in
the workflow when the triage node selects them for a given task; they can
also be invoked manually via "Ask". CEO and GTM are manual only. Manual
roles never modify code and never trigger chains of other agents.

## Testing

```bash
# Backend tests (no live model required)
cd backend
uv sync
uv run pytest                # 132 tests

# Frontend type-check and build
cd web
npm run build

# End-to-end tests (requires the backend running on :8010)
#   cd backend && SCENEWORKS_DEFAULT_BACKEND=fake uv run uvicorn app.main:app --port 8010
npm run test:e2e             # 12 tests
```

> `npm run lint` is declared in `package.json` but **no ESLint configuration
> exists** in this repository — running it starts an interactive setup prompt
> rather than linting. There is no configured linter for either the backend or
> the frontend; type checking happens as part of `npm run build`.

### How to read validation claims

Every capability below is labelled with the strongest evidence that exists
for it. The labels are deliberately narrow.

| Label | Meaning |
| --- | --- |
| `implemented` | Code exists and is exercised by no automated test. |
| `automatically tested` | Covered by `uv run pytest` against the scripted FakeAgentBackend. |
| `browser-E2E validated` | Exercised through a real Chromium page in `web/e2e/`. |
| `API-E2E validated` | Exercised end-to-end against the running server via Playwright's HTTP client — a real server and real Git, but no browser UI. |
| `live-model validated` | Executed against the real Gemini CLI over ACP, with real Git worktrees. |
| `experimental` | Present but never validated; may not work. |

Current state of the E2E suite (12 tests): the workflow scenarios
(architecture → approval → engineer → reviewer → accept, revision,
rejection, cancellation, repair loop, project memory, company ask) are
**API-E2E validated** — they drive the running server over HTTP rather than
clicking through the UI. Five tests (dashboard, projects, company, settings,
API-unreachable handling) are **browser-E2E validated**.

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
  prompting, event mapping, fs-write/shell denial for read-only roles,
  permission boundaries for all 8 roles (Architect, Engineer, Reviewer,
  Technical Expert, CEO, CTO, Product, GTM), unknown capability fail-closed,
  cancellation.
- **Workflow graph tests** (`test_workflow_graph.py`): LangGraph topology,
  persistence, approval/rejection/revision, auto-repair, repair limits,
  idempotency, worktree continuity, cancellation.
- **OpenHands tests** (`test_openhands.py`): Contract validation,
  health checks, configuration, registry integration, experimental label.
- **Memory tests** (`test_memory.py`): CRUD, search, archival,
  injection context, provenance.
- **End-to-end tests** (`web/e2e/`): Playwright, against a running server
  and real Git repositories created per test, using FakeAgentBackend.
  Seven workflow tests drive the API directly (`API-E2E validated`); five
  navigate real pages in Chromium (`browser-E2E validated`). No live models.
  They run against the production build (`next build && next start`); set
  `E2E_DEV=1` to use the dev server instead.
- **Live-model validation** (manual, not part of `pytest`): run the app
  against a real repository with `SCENEWORKS_DEFAULT_BACKEND=gemini_acp`
  and execute a task as described above. See "V3.0 baseline" for what has
  actually been exercised this way.

## Documentation

- [Quick Start](docs/quickstart.md) — First 10 minutes guide from zero to
  running SceneWorks.
- [Architecture](docs/architecture.md) — Component responsibilities,
  dependency boundaries, task lifecycle, event flow.
- [Web UI Tutorial](docs/tutorials/web-ui.md) — Step-by-step guide from
  installation to accepting a task result.
- [Adding a Backend](docs/backends.md) — Guide for implementing new
  AgentBackend providers, and the OpenHands validation status.
- [Development](docs/development.md) — Developer setup, testing, and CI.
- [Known Limitations](docs/limitations.md) — Current boundaries and
  non-goals.
- [Qualification](docs/qualification.md) — The engineering-outcome
  evaluation suite and its go/no-go contract.
- [Project Memory](docs/memory.md) — Deterministic retrieval, the
  authoritative/speculative lifecycle, and provenance.
- [Project Policy](docs/project-policy.md) — The engineering-contract
  abstraction, deterministic protected-path checking, and a worked example.
- [Operations](docs/operations.md) — Versioning, database migrations,
  backup/restore, recovery semantics, and CI.
- [Baseline audit](docs/wp0-baseline-audit.md) — The evidence-based audit
  this roadmap started from.
- [OpenHands validation](docs/wp2.5-openhands-validation.md) — What was
  actually installed, configured, and run against a real backend.

## V3.0 baseline

V3.0 contains no new product capability. It is an independent audit of the
V2.5.1 system: the defects it found are fixed, and every claim below is
labelled with the evidence that actually exists for it.

### Correctness fixes

- **Agents could reach the human working tree.** The ACP file-system proxy
  accepted any path under the repository root in addition to the worktree.
  A read-only role could therefore read uncommitted human edits, and the
  Engineer — which has write permission — could write into the human
  checkout. Access is now confined to the pinned worktree, and shell working
  directories are validated the same way.
- **Manual company asks read the mutable working tree.** A repository-grounded
  ask ran with `cwd` set to the human checkout, so uncommitted changes could
  silently enter an answer and no commit was recorded. Asks now run in a
  commit-pinned detached worktree, record `base_commit`, and state the
  analyzed commit in the stored decision.
- **Triage read the mutable working tree** for the same reason, and pinned no
  commit. Triage now runs in a detached worktree and pins `task.base_commit`
  once for the whole workflow; the Architect reuses it instead of re-resolving
  the branch head (which let the base drift on the revision loop).
- **`terminal/wait_for_exit` always failed** — `self._REQUEST_TIMEOUT` on an
  attribute that only exists at module level. Every attempt by an agent to
  wait for a command it started raised `AttributeError`.
- **Auto-repair crashed the workflow.** On Windows `git worktree remove`
  routinely leaves an empty directory behind, because the agent process that
  had it as its cwd is still exiting. The second review iteration then hit
  "worktree path already exists", and `GitError` — unlike `WorkflowError` —
  was not caught by the graph nodes, so the whole task went to `FAILED`.
  Stale leftovers are now reclaimed (registered worktrees are still never
  overwritten), and git failures degrade to a clean task failure.
- **Replayed side effects.** `_finish_architect/_engineer/_reviewer` applied
  their state transition unconditionally, so re-entering a node after a
  restart raised `InvalidTransition` and failed the task. Each now applies its
  transition only from the state it is valid in.
- **`retry` was broken for architecture failures**: `FAILED → retry` produced
  `READY_TO_IMPLEMENT`, from which `start_architecture` is not a legal
  transition. A task with no architecture now retries via `retry_architecture`
  back to `NEW`.
- **Engineer runs that produced no commit** were recorded as if they had:
  `result_commit` was set to the base commit and a `git.commit` event
  announced it. Uncommitted work left in the worktree is now committed on the
  Engineer's behalf, and a commit event is only emitted for a real commit.
- **Health probes blocked the UI.** `/api/backends` and `/api/settings` shelled
  out to `gemini --version` on every request (~20 s and ~28 s measured here),
  gating first paint on the Dashboard and Settings pages. Health is now cached
  and served stale-while-revalidate, warmed at startup: the same requests
  measure ~50 ms and ~14 ms.
- **Terminal status was written before the terminal event**, so a client could
  observe an execution `COMPLETED` and then fetch events that did not yet
  contain `execution.completed`. They now commit in one transaction.
- **No agent activity was visible anywhere.** `AgentEventSink.emit` did not
  pass its execution id to the engine's emitter, so every agent event was
  persisted with `execution_id` and `task_id` NULL. 1,325 such rows had
  accumulated: text deltas, tool calls, file changes, command output — all
  unreachable from `/api/tasks/{id}/events` and `/api/executions/{id}/events`,
  so the "live event log" showed only lifecycle transitions during real runs.
- **Timeouts sized for toy repositories.** `git worktree add` was capped by a
  hard-coded 120 s constant and aborted on a large repository, failing the
  task before triage could start; the git timeout is now configurable
  (`SCENEWORKS_GIT_TIMEOUT_SECONDS`, default 300 s — down from 900 s now that
  fsmonitor daemon accumulation is suppressed per-process). The ACP `initialize`
  handshake exceeded its 30 s budget whenever two agents started at once
  (now 120 s), and a single Engineer run exceeded the 1800 s execution limit
  while running the project's own test and lint commands (now 5400 s).
- **Memory events were dropped by SSE.** `MemoryService` published `"id": 0`;
  because the SSE stream de-duplicates on id, every memory event after the
  first was discarded. Workflow events also published no `timestamp`, which
  the event log renders.
- **Architect manual asks produced no artifact**: artifact storage keyed off
  `COMPANY_ROLES`, which excludes `architect` even though it is ask-allowed.
- Smaller fixes: `send_back_to_engineer` wrote `project_id: 0` into graph
  state and did not reset the repair budget; advisory worktrees leaked when an
  execution failed; the advisory-only path double-transitioned through
  `AWAITING_ARCHITECTURE_APPROVAL`; dead `TaskWorkflowService` transition code
  removed; unbounded SSE de-duplication set bounded.

### What was actually validated, and how

| Area | Evidence |
| --- | --- |
| Backend behaviour | `automatically tested` — 120 tests |
| Workflow scenarios incl. repair loop | `API-E2E validated` |
| Dashboard / projects / company / settings pages | `browser-E2E validated` |
| Gemini ACP: triage → architect → engineer → reviewer, real Git | `live-model validated` |
| Repository snapshot pinning under a concurrent human commit | `live-model validated` |
| OpenHands backend | `experimental` — never executed |
| `model_profile` → model routing | not implemented; see below |

### Model configuration

There is no hard-coded model anywhere. `SCENEWORKS_GEMINI_MODEL` is unset by
default, so SceneWorks exports no `GEMINI_MODEL` and the Gemini CLI performs
its own automatic model selection (backend health reports `model: auto`).
Setting the value pins every role to that model.

`model_profile` (`strongest` / `coding` / `research`) is **metadata only**. It
is recorded on the role and on each execution row for provenance, but no
backend reads it and it does not influence model selection. Profile→model
routing is deferred to V3.1 provider routing.

## V2.5.2 — Final V2 closure

V2.5.2 closes the remaining production blockers discovered during real PCS usage.

### Gemini ACP capability mediation

- Full [Gemini capability matrix](docs/gemini-capability-matrix.md) classifies
  all 23 Gemini ACP capability categories across 8 ACP client methods, 6 Gemini
  internal tool types, and 4 notification classes.
- **Unknown capability fail-closed**: Any ACP client method not in the known set
  receives an error response and is recorded as a `capability_denied` diagnostic
  event.
- **Permission boundary tests**: 11 new tests verify that Architect, Technical
  Expert, Reviewer, and Engineer cannot exceed their permissions through any
  supported ACP client method (fs/read, fs/write, terminal/create,
  session/request_permission). CEO write-denial also tested.

### fsmonitor process leak (fixed)

- **Root cause**: When the managed repository has `core.fsmonitor=true`, every
  git subprocess spawned by SceneWorks (worktree create/destroy, agent shell
  commands) starts a persistent `git fsmonitor--daemon` that outlives the
  parent. 308 orphans accumulated during one PCS engineer+reviewer cycle.
- **Fix**: `GIT_CONFIG_PARAMETERS` set per-process to suppress `core.fsmonitor`
  for all SceneWorks git operations and agent terminal commands. The user's
  global and repository-level `core.fsmonitor` is never modified.
- **Stress test**: 20 worktree create/destroy cycles with fsmonitor counting
  verifies no unbounded accumulation (passed).
- **Before/after**: git timeout reduced from 900 s to 300 s now that the
  daemon slowdown is eliminated.

### Timeout policy (reviewed)

| Setting | Default | Rationale |
|---|---|---|
| `SCENEWORKS_GIT_TIMEOUT_SECONDS` | 300 s | Worktree checkout on ~30k-file repo measured ~45 s with fsmonitor suppressed |
| `SCENEWORKS_GEMINI_STARTUP_TIMEOUT_SECONDS` | 120 s | Node startup ~20 s cold; ACP handshake up to 30 s concurrent |
| `SCENEWORKS_EXECUTION_TIMEOUT_SECONDS` | 5400 s | Real engineer runs exceed 1800 s iterating on project test/lint commands |
| `SCENEWORKS_CANCEL_GRACE_SECONDS` | 15 s | Grace period before engine force-kill |

All timeouts remain configurable via environment variables.

### Backend health cold-cache fix

The `/api/backends` and `/api/settings` endpoints previously blocked on the
first health probe (~20-120 s for Gemini `--version` on cold cache). Now
return placeholder "probing..." status immediately while the background probe
runs; real results appear on the next request.

### Evidence tiers

| Area | Evidence |
|---|---|
| Backend behaviour | `automatically tested` — 132 tests (was 120) |
| Gemini permission boundaries | `automatically tested` — 11 new ACP proxy tests |
| fsmonitor suppression | `automatically tested` — 20-cycle stress test |
| Workflow scenarios incl. repair loop | `API-E2E validated` |
| Dashboard / projects / company / settings pages | `browser-E2E validated` |
| Gemini ACP: triage → architect → engineer → reviewer, real Git | `live-model validated` |
| Repository snapshot pinning under concurrent human commit | `live-model validated` |

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
- **Evaluation framework**: scenario *definitions* for bug fixes, features,
  refactors and architecture decisions were added, but the V2.5 runner never
  executed a workflow — it reported PASS whenever setup did not raise. The
  WP0 audit ([docs/wp0-baseline-audit.md](docs/wp0-baseline-audit.md), F1/F2)
  documents this; it is replaced in V3 by the qualification suite
  ([docs/qualification.md](docs/qualification.md)).

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
