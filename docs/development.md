# Development

## Developer setup

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (Python dependency manager)
- Node.js >= 20 with npm
- Git

### Clone and install

```bash
git clone <repo-url> sceneworks
cd sceneworks

# Backend
cd backend
uv sync

# Frontend
cd ../web
npm install
```

### Run in development

**Backend** (terminal 1):
```bash
cd backend
uv run python -m app.main
```

This binds the port configured in `SCENEWORKS_PORT` (default **8010**), the
port the web client targets. Do not use the bare
`uv run uvicorn app.main:app` form — uvicorn's CLI default port is 8000, and
the mismatch makes every browser request fail with
"TypeError: Failed to fetch". If you need the CLI directly, pass the port
explicitly: `uv run uvicorn app.main:app --reload --port 8010`.

**Frontend** (terminal 2):
```bash
cd web
npm run dev
```

Open http://localhost:3000

### Running tests

```bash
cd backend
uv run python -m pytest                    # all tests (live tests skip if unconfigured)
uv run python -m pytest -m "not slow"      # skip full-workflow tests
uv run python -m pytest -m "not live"      # skip real-provider tests
uv run python -m pytest tests/test_api.py  # specific test file
uv run python -m pytest -k "openhands"     # tests matching pattern
uv run python -m pytest --collect-only     # list all tests
```

The test suite uses a temporary file-backed SQLite database (one per test, under
pytest's `tmp_path`) and the FakeAgentBackend. It never requires a live Gemini
CLI or an OpenHands server.

Baseline as of V3.0.0 (WP3): **286 tests, ~795 s** (`-m "not live"`; 2 further
tests skip cleanly without a real provider configured). The Gemini ACP,
workflow-graph, qualification and migration tests dominate that runtime. Tests
marked `slow` drive a whole workflow through the qualification harness
(worktrees, subprocesses, LangGraph) and take tens of seconds each; deselect
them with `-m "not slow"`.

### Qualification (go/no-go)

Separate from the unit suite: the qualification suite drives real workflows and
judges **engineering outcomes**, then reports a machine-readable GO/NO-GO result.

```bash
cd backend
uv run python -m evaluation                # full suite, 19 scenarios (~2.5-6 min)
uv run python -m evaluation --smoke        # CI subset, 5 scenarios (~60 s)
uv run python -m evaluation --list
uv run python -m evaluation --scenario bug-fix -v
uv run python -m evaluation --json qualification.json
```

Exit codes: `0` PASS, `1` FAIL, `2` BLOCKED (including any partial run — a subset
cannot qualify a release), `3` NOT_RUN, `4` usage error. Full contract in
[qualification.md](qualification.md).

### Live provider testing

Tests marked `live` need a real agent provider and a real model. They skip
cleanly when it is not configured and never fall back to a fake, so an
unavailable provider can never be reported as a pass.

```bash
# OpenHands (experimental; read-only roles only on Windows)
cd backend && uv sync --extra openhands
export SCENEWORKS_OPENHANDS_MODEL=lm_studio/google/gemma-4-e2b
export SCENEWORKS_OPENHANDS_BASE_URL=http://127.0.0.1:1234/v1
uv run python -m pytest -m "live and openhands"

# Qualification scenarios against a real backend
uv run python -m evaluation --backend openhands --live-subset
uv run python -m evaluation --backend gemini_acp --scenario cancellation
```

A live run against a small local model is slow: a single read-only file
inspection measured **431 s** with `gemma-4-e2b` via LM Studio. Budget
accordingly, and prefer the `cancellation` scenario for a fast smoke of the live
path — it does not wait for the model to finish.

### End-to-end tests

```bash
# Terminal 1: Start the backend with the scripted fake backend
cd backend
SCENEWORKS_DEFAULT_BACKEND=fake uv run python -m app.main

# Terminal 2: Run the E2E suite (it starts the web server itself)
cd web
npx playwright test
```

Playwright starts the web server for you: by default it runs
`next build && next start`, so the suite exercises the production build. Set
`E2E_DEV=1` to use `next dev` instead — useful while iterating, but the dev
server compiles routes on first visit, which can exceed assertion timeouts.

The backend must already be running on port 8010; Playwright does not start
it. E2E tests create their own temporary Git repositories, so no
`E2E_REPO_PATH` environment variable is needed.

Only some of these are browser tests. The workflow scenarios drive the API
over HTTP through Playwright's request client; the dashboard, projects,
company, settings and error-handling tests navigate real pages. See the
validation-label table in the README.

### Continuous integration

Two GitHub Actions workflows, split by cost:

- `.github/workflows/ci.yml` -- fast gate, every push to `master`: import
  check, `pytest -m "not slow and not live"`, qualification `--smoke`,
  frontend `next build`.
- `.github/workflows/release.yml` -- full validation, manual dispatch or a
  `v*` tag: the complete backend suite, migration validation, full
  qualification (19 scenarios), and Playwright E2E against a live backend.

Neither runs `live`-marked tests: a GitHub-hosted runner has no Gemini CLI
authentication and no local LLM endpoint. Full contract, including why the
gate is split this way, in [operations.md](operations.md).

### Code conventions

- **No comments** unless necessary for architectural clarity
- Follow existing patterns in each module
- Async-first: all I/O is `async def`
- Type hints on all function signatures
- No bare exceptions — always specify exception types
- Event payloads are plain JSON dicts
- Use `from __future__ import annotations` in all Python files

### Project structure conventions

| Concern | Pattern |
|---|---|
| API routes | `app/api/<resource>.py`, uses FastAPI dependency injection |
| Domain logic | Stateless functions, no I/O |
| Services | Stateful classes with `_session_factory` for DB access |
| Agents | Single-file adapter implementing `AgentBackend` protocol |
| Events | Constants in `types.py`, labels for UI, store for persistence |
| Git | Subprocess-based via `asyncio.create_subprocess_exec` |

### Adding a new role

1. Add `RoleDefinition` in `backend/app/roles/definitions.py`
2. Add `backend/app/roles/prompts/<key>.md` with instructions
3. Expose in the company API automatically

### Adding a new workflow node

Workflow nodes live in `backend/app/workflows/manager.py` as methods on
`WorkflowManager`. They are thin adapters that:
1. Receive LangGraph state
2. Delegate to TaskWorkflowService or ExecutionEngine
3. Return updated state or LangGraph `Command`

### Adding a new API route

1. Create or extend a module in `backend/app/api/`
2. Register the router in `backend/app/main.py`
3. Add Pydantic schemas in `backend/app/schemas.py` if needed
4. Add SQLAlchemy models in `backend/app/models.py` if needed

### Adding a new event type

1. Add constant in `app/events/types.py`
2. Add label in `EVENT_LABELS` dict
3. Emit through `EventStore.append()` or `EventBus.publish()`

### Frontend development

The frontend is a Next.js 15 App Router application with TypeScript.

- Pages: `web/app/<route>/page.tsx`
- Shared components: `web/components/`
- API client: `web/lib/api.ts`
- Types: `web/lib/types.ts`
- Styles: `web/app/globals.css`

Type-check and build:
```bash
cd web
npm run build
```

> There is **no configured linter** in this repository. `package.json` still
> declares a `lint` script, but no ESLint configuration exists, so
> `npm run lint` opens an interactive setup prompt instead of linting. Do not
> treat it as a validation step. Type errors are caught by `npm run build`.
