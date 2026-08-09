# SceneWorks V1

SceneWorks is an **AI-native software company control plane**: a standalone web
application for operating software projects through virtual company roles
(CEO, CTO, Chief Architect, Product, Engineer, Reviewer/QA, GTM) while a human
founder remains the final authority.

SceneWorks is independent from any managed product or repository. Managed
repositories never know SceneWorks exists: SceneWorks code is never added to
them, and agents only ever work in isolated Git worktrees outside the human
working tree.

```
Browser
   │
   ▼
SceneWorks Web (Next.js)
   │  REST + SSE
   ▼
SceneWorks API (FastAPI) ──► SQLite (state, executions, events)
   │
   ▼
SceneWorks Worker (in-process for V1)
   ├── Gemini CLI (via ACP, stdio)   ← first AgentBackend
   ├── Git (worktrees, commits, diffs)
   ├── shell/test commands (mediated by the ACP file/terminal proxy)
   └── managed repositories (read/write only inside worktrees)
```

## Architecture in one paragraph

SceneWorks defines the company and the workflow; roles define responsibilities
and permissions; an `AgentBackend` abstraction defines execution capability.
Gemini CLI over ACP is the first backend implementation and is fully contained
in `backend/app/agents/gemini_acp.py`. Tasks move through an explicit state
machine (`NEW → ARCHITECTURE_ANALYSIS → AWAITING_ARCHITECTURE_APPROVAL →
READY_TO_IMPLEMENT → IMPLEMENTING → TESTING → REVIEWING → CHANGES_REQUESTED
→ … → READY_FOR_HUMAN → ACCEPTED/REJECTED`, plus FAILED/CANCELLED). Every agent
invocation is a persistent `Execution` that streams structured events; the UI
shows them live over Server-Sent Events. Nothing merges into the human branch
automatically.

Key components:

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
| AgentBackend protocol + fake backend | `backend/app/agents/base.py`, `backend/app/agents/fake.py` |
| Gemini ACP backend (ACP client + proxy) | `backend/app/agents/gemini_acp.py` |
| Backend registry | `backend/app/agents/registry.py` |
| Execution engine (async, cancellation, recovery) | `backend/app/execution/engine.py` |
| Event bus + durable event store | `backend/app/events/bus.py`, `backend/app/events/store.py` |
| Workflow orchestration (Architect→Engineer→Reviewer) | `backend/app/services/workflow.py` |
| Company ask service | `backend/app/services/company.py` |
| API routes | `backend/app/api/` |
| Frontend (Next.js app router) | `web/app/`, `web/components/`, `web/lib/` |

## Setup

Requirements: Python 3.12, Node.js >= 20, Git.

### Backend

```bash
cd backend
python -m venv .venv
.\.venv\Scripts\activate        # Windows; use `source .venv/bin/activate` on macOS/Linux
pip install -r requirements.txt
copy ..\.env.example .\.env    # optional; or set SCENEWORKS_* env vars
uvicorn app.main:app --reload   # http://127.0.0.1:8010  (docs at /docs)
```

### Frontend

```bash
cd web
npm install
npm run dev                     # http://localhost:3000
```

The frontend calls the API at `http://127.0.0.1:8010` (override with
`NEXT_PUBLIC_API_URL`). CORS is configured for `http://localhost:3000`.

## Registering a repository

Projects page → **Add existing repository** → enter the absolute path of a
local Git repository. SceneWorks validates that the path exists and is a Git
repository, captures the current head branch, and never touches its files.
A second, third, any arbitrary repository can be added the same way — there is
no project-specific code anywhere.

Configure `test_commands` (one per line) and optional context file paths
(e.g. `docs/architecture.md`); the default context files
`AGENTS.md`, `ARCHITECTURE.md`, `CONTRIBUTING.md`, `ROADMAP.md` are read
automatically if present at the repository root.

## Configuring Gemini CLI

1. Install and authenticate the Gemini CLI (`npm install -g @google/gemini-cli`,
   `gemini` → sign in).
2. Backend health is shown on the Settings page (`Gemini ACP: Available`,
   with the CLI version). If it is unavailable, SceneWorks still runs — the
   backend is only used when a role executes.
3. Optional environment variables (or Settings page):
   - `SCENEWORKS_GEMINI_EXECUTABLE` — override the executable path.
   - `SCENEWORKS_GEMINI_MODEL` — model preference (passed to the CLI session).
   - `SCENEWORKS_GEMINI_EXTRA_ARGS` — extra CLI arguments.
   - `SCENEWORKS_EXECUTION_TIMEOUT_SECONDS` — hard execution timeout.
   - `SCENEWORKS_WORKTREE_ROOT` — where agent worktrees are created (must be
     outside managed repositories; default `backend/data/worktrees`).

Gemini is launched in ACP mode (`--acp`, protocol version 1 over stdio).
SceneWorks mediates every file read/write and command through the ACP client
proxy, enforcing role permissions: the read-only Architect cannot write files
or run commands; the Engineer can edit and run commands only inside its
worktree; the Reviewer can run validation commands but not modify source.

## Executing the first task (end-to-end)

1. **Tasks → New task** on a registered project.
2. **Run Architect analysis** — the Architect (read-only, in a detached
   worktree) inspects the repository and task and produces a structured
   architecture analysis.
3. **Approve architecture** (or reject / request revision).
4. **Start implementation** — SceneWorks creates the task branch
   (`sw-task-<id>`) and an isolated worktree under the worktree root, then
   launches the Engineer. The Engineer edits files, runs commands/tests, and
   commits on the task branch. Your working tree is never touched.
5. **Start review** — the Reviewer inspects the commit/diff and tests in a
   disposable worktree and either approves
   (`VERDICT: APPROVED` → `READY_FOR_HUMAN`) or requests changes
   (`CHANGES_REQUESTED` → back to the Engineer).
6. **Accept** (or reject / send back) — SceneWorks never merges automatically.
   You decide what to integrate from the worktree branch.

The task detail page streams activity live, shows the architecture result,
implementation summary, review result, the complete diff, and offers the
applicable actions for the current state.

## Company roles

The Company page shows the org (Founder → CEO → CTO/Product/GTM, Chief
Architect → Engineer, Reviewer/QA). Roles are configuration — purpose,
permissions, backend, model profile — not running processes. CEO/CTO/Product/
GTM are invoked manually ("Ask CTO …"), use the project context you select,
and their outputs are stored as company decisions/artifacts. They never
modify code and never trigger chains of other agents.

## Adding another AgentBackend

1. Create `backend/app/agents/<name>.py` implementing the
   `AgentBackend` protocol from `app/agents/base.py`:
   `run(request, workspace, event_sink)`, `cancel(execution_id)`, `health()`.
   Emit only the generic event vocabulary from `app/events/types.py`.
2. Register it in `app/agents/registry.py`.
3. Point a role at it via configuration (see `RoleRegistry.effective`) or the
   `default_backend` setting.

Coding-agent runtimes (Gemini CLI, Claude Code, Codex) and direct LLM APIs are
both supported concepts: a direct model backend simply implements the same
protocol without filesystem/shell capabilities.

## Adding another company role

1. Add a `RoleDefinition` in `backend/app/roles/definitions.py` (permissions,
   backend, model profile, responsibilities).
2. Add `backend/app/roles/prompts/<key>.md` with its standing instructions
   (editable without code changes).
3. If it should run task phases, wire its execution into the workflow service;
   for manual "Ask <role>" invocations it works out of the box.

## Testing

```bash
cd backend
pip install -r requirements-dev.txt
python -m pytest            # 59 tests, no live model access required
```

- **Unit tests**: state machine, permissions, roles, prompt/context limits.
- **Git integration tests**: temporary repositories — worktree creation,
  branch creation, commits, diffs, cleanup, dirty-tree isolation.
- **Engine tests**: execution lifecycle, events, cancellation, restart
  recovery, worktree continuation (scripted FakeAgentBackend).
- **API tests**: projects, task actions, invalid transitions (409s), the full
  Architect→Engineer→Reviewer workflow, changes-requested loop, company asks,
  dashboard.
- **ACP protocol tests**: `tests/mock_acp_server.py` implements the ACP v1
  agent side; tests verify prompting, event mapping, fs-write denial for
  read-only roles, permission responses, and cancellation.

A live Gemini smoke test is optional: run the app with a real project and
execute a task as described above. Tests themselves never call Gemini.

## Security and trust assumptions (V1)

- The API binds to localhost by default. It is a trusted control plane, not a
  public service — there is no user authentication. Do not expose it beyond
  localhost.
- Agents run on the same machine as the API, inside isolated Git worktrees.
  File access and commands are mediated by the ACP client proxy, which
  enforces role permissions and path boundaries (worktree + repository).
- Commands run with the SceneWorks process's user permissions. This is not an
  OS-level sandbox; treat the worker machine as trusted.
- Agent output is always reviewable (events, logs, diffs) and nothing is
  merged automatically.
- Secrets never live in the frontend or in SceneWorks settings; the Gemini
  CLI uses its own authentication.

## Known V1 limitations

- Single-machine deployment: API + worker + SQLite run together. The worker
  abstraction is isolated so remote workers can be added later, but there is
  no distributed execution.
- No user accounts/teams, no GitHub/PR integration, no automatic merging.
- Context selection is direct file reading (no RAG/embeddings); context
  files are capped at 60 KB each / 200 KB total.
- On Windows the Gemini CLI is spawned with its own console window, which its
  shell tool requires; a window may briefly appear while an agent runs.
- The Reviewer's shell access is enforced via prompt + disposable worktree,
  not an OS sandbox.
- Executions interrupted by a SceneWorks restart are marked `INTERRUPTED`
  (task → `FAILED`); retry re-enters the appropriate phase.
- Two Gemini instances may not run concurrently in the same user profile on
  Windows; executions run sequentially on the single worker.
