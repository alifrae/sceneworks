# WP21 Lifecycle Supervisor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an out-of-process, crash-loop-safe local lifecycle supervisor that owns SceneWorks API, web, and MCP-tunnel recovery; expose narrow UI/MCP controls; and make the existing Windows launcher delegate lifecycle ownership to it.

**Architecture:** A standard-library Python package under `supervisor/` owns component state, process/health providers, durable SQLite operation journaling, monitoring/recovery, and a loopback JSON HTTP API on `127.0.0.1:8020`. FastAPI accesses it through a narrow `SupervisorClient`; Next.js uses server-only proxy routes so the browser never sees the mutation token. The Windows launcher remains bootstrap/provisioning glue but starts the supervisor and delegates start/restart/reconcile operations instead of killing services itself.

**Tech Stack:** Python 3.12 standard library (`http.server`, `sqlite3`, `subprocess`, `threading`, `urllib`), existing FastAPI/httpx backend, Next.js 15/React 19, PowerShell launcher, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-05-wp21-lifecycle-supervisor-design.md`

## Global Constraints

- WP21 manages only `api`, `web`, and `mcp_tunnel`.
- The supervisor process must be outside FastAPI and must survive API restart.
- Supervisor binds to loopback only at `127.0.0.1:8020`.
- Browser JavaScript must never receive the supervisor bearer token.
- Remote/UI/MCP calls must never accept arbitrary shell, PID, port, URL, executable path, environment dictionary, or command-string payloads.
- A port match alone is never authority to terminate a process; ambiguous ownership fails closed.
- Automatic recovery is bounded to 3 attempts in rolling 5 minutes with 1/2/5-second retry delays; 10 continuous healthy minutes clear the budget.
- Health monitor cadence is 5 seconds; healthy components require 3 consecutive failed samples before recovery unless their owned process exited.
- Default startup grace: API 45 s, web 60 s, MCP tunnel 20 s.
- Mutating lifecycle operations are durably accepted before execution and return an `operation_id`.
- Supervisor journal stores bounded operational metadata only and never persists credentials or environment dumps.
- No arbitrary download-and-execute behavior is introduced.
- Existing provider/runtime/evidence/PCS/GUI invariants in `AGENTS.md` remain valid.

---

### Task 1: Supervisor domain, providers, state machine, and bounded recovery

**Files:**
- Create: `supervisor/__init__.py`
- Create: `supervisor/model.py`
- Create: `supervisor/providers.py`
- Create: `supervisor/core.py`
- Create: `supervisor/tests/__init__.py`
- Create: `supervisor/tests/test_core.py`

**Interfaces:**
- Produces `ComponentKey`, `ComponentState`, `OperationResult`, `ComponentStatus`, and `ComponentSpec` in `supervisor.model`.
- Produces `ProcessProvider`/`HealthProvider` protocols plus deterministic fakes and `WindowsProcessProvider`/`HttpHealthProvider` in `supervisor.providers`.
- Produces `LifecycleSupervisor.status()`, `start()`, `stop()`, `restart()`, `restart_all()`, `reconcile()`, and `monitor_once()` in `supervisor.core`.

- [ ] **Step 1: Write failing core tests**

Create `supervisor/tests/test_core.py` with tests that assert: three failed health samples trigger one automatic restart, observed owned-process exit triggers immediate recovery, three automatic attempts inside five minutes transition to `DEGRADED`, ten healthy minutes clear the budget, `restart_all()` orders `mcp_tunnel -> web -> api -> api -> web -> mcp_tunnel`, and ambiguous ownership is never stopped.

- [ ] **Step 2: Verify RED**

Run from repository root:

```powershell
python -m unittest supervisor.tests.test_core -v
```

Expected: import failures because `supervisor.model/providers/core` do not yet exist.

- [ ] **Step 3: Implement minimal domain and provider contracts**

`ComponentKey` is a `StrEnum` with `api`, `web`, `mcp_tunnel`; `ComponentState` contains `STOPPED`, `STARTING`, `HEALTHY`, `UNHEALTHY`, `RECOVERING`, `DEGRADED`, `UNKNOWN`. `ProcessProvider` exposes fixed semantic component methods rather than caller-provided commands. `FakeProcessProvider` records semantic start/stop calls for deterministic tests.

- [ ] **Step 4: Implement `LifecycleSupervisor` core behavior**

Use injected clock/sleep/process/health providers. Keep state and restart history under a lock. Enforce 3 consecutive health failures, 3-in-5-minute automatic restart budget, 1/2/5-second retry delays, ten-minute healthy reset, safe ownership refusal, and the exact `restart_all()` dependency order from the spec.

- [ ] **Step 5: Verify GREEN**

Run:

```powershell
python -m unittest supervisor.tests.test_core -v
```

Expected: all Task-1 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add supervisor

git commit -m "feat: add lifecycle supervisor core"
```

---

### Task 2: Durable journal, token management, asynchronous operation worker, and loopback HTTP API

**Files:**
- Create: `supervisor/journal.py`
- Create: `supervisor/http_api.py`
- Create: `supervisor/__main__.py`
- Create: `supervisor/tests/test_journal.py`
- Create: `supervisor/tests/test_http_api.py`
- Modify: `supervisor/core.py`

**Interfaces:**
- Produces `OperationJournal.accept(actor, action, component) -> operation_id`, `mark_running()`, `finish()`, `get()`, and `list()`.
- Produces `SupervisorApplication.submit(action, component, actor) -> operation_id` and background worker/monitor lifecycle.
- HTTP: `GET /v1/status`, `GET /v1/operations?limit=N`, `GET /v1/operations/{id}`, `POST /v1/actions/{start|stop|restart|reconcile}` and `POST /v1/actions/restart-all`.
- Mutation authorization: `Authorization: Bearer <token>`; reads stay loopback/read-only.

- [ ] **Step 1: Write failing journal/API tests**

Tests must assert that mutation is rejected without the token, acceptance is persisted before worker execution, SQLite rows survive a new `OperationJournal` instance, secret-looking environment data is absent from serialized operation/status payloads, `restart-all` returns `202` with an operation id, and unsupported components/actions return `400`/`404` without execution.

- [ ] **Step 2: Verify RED**

Run:

```powershell
python -m unittest supervisor.tests.test_journal supervisor.tests.test_http_api -v
```

Expected: missing journal/API symbols.

- [ ] **Step 3: Implement SQLite journal and local-data/token helpers**

Default Windows directory is `%LOCALAPPDATA%\SceneWorks\supervisor`; non-Windows fallback is `~/.local/share/sceneworks/supervisor`. Generate a 32-byte URL-safe token only when absent. Store token in `token` and operation DB in `supervisor.db`; attempt user-only file mode where supported.

- [ ] **Step 4: Implement asynchronous application worker**

`submit()` writes accepted operation first, then queues it. The worker updates `RUNNING` and final `SUCCEEDED|FAILED|PARTIAL|REJECTED`. Monitoring runs every five seconds and submits automatic recovery through the same journaled path.

- [ ] **Step 5: Implement loopback HTTP server and CLI entry point**

Use `ThreadingHTTPServer`; reject non-loopback bind configuration in WP21. CLI arguments are bootstrap-only (`--repo-root`, `--no-tunnel`, `--tunnel-client-path`, `--mcp-server-url`, `--port`) and are not exposed through HTTP/MCP.

- [ ] **Step 6: Verify GREEN**

Run:

```powershell
python -m unittest discover -s supervisor/tests -v
```

Expected: all supervisor tests PASS.

- [ ] **Step 7: Commit**

```bash
git add supervisor

git commit -m "feat: add supervisor journal and control api"
```

---

### Task 3: Windows launcher delegates lifecycle ownership to the supervisor

**Files:**
- Modify: `scripts/start-sceneworks.ps1`
- Modify: `docs/development/windows-launcher.md`
- Create: `supervisor/tests/test_launcher_contract.py`

**Interfaces:**
- Launcher starts/reuses supervisor at `127.0.0.1:8020`.
- Launcher sends authenticated supervisor operations using the local token file.
- `-Restart` maps to `restart-all`; normal launch maps to `reconcile`/start semantics.
- Existing dependency sync/frontend build behavior remains bootstrap-only.

- [ ] **Step 1: Write failing launcher-contract tests**

Use a source-level contract test that reads `scripts/start-sceneworks.ps1` and asserts the normal path contains supervisor startup/readiness and authenticated lifecycle calls, while the old `Restart-SceneWorksStack` direct kill path is absent from normal restart handling.

- [ ] **Step 2: Verify RED**

Run:

```powershell
python -m unittest supervisor.tests.test_launcher_contract -v
```

Expected: FAIL because launcher still directly owns restart.

- [ ] **Step 3: Refactor launcher**

Keep `uv sync`/`npm ci`/production build checks. Start the supervisor out of process from repository root using the existing backend Python environment (`uv run --project backend python -m supervisor ...`), wait on `/v1/status`, read the local token only inside PowerShell, submit `restart-all` for `-Restart` or `reconcile` for normal startup, and wait until required components report healthy. Never pass the token to the browser.

- [ ] **Step 4: Verify GREEN**

Run supervisor tests plus PowerShell syntax parsing on Windows:

```powershell
python -m unittest supervisor.tests.test_launcher_contract -v
powershell -NoProfile -Command "[void][scriptblock]::Create((Get-Content -Raw scripts/start-sceneworks.ps1))"
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/start-sceneworks.ps1 docs/development/windows-launcher.md supervisor/tests/test_launcher_contract.py

git commit -m "feat: delegate launcher lifecycle to supervisor"
```

---

### Task 4: Backend `SupervisorClient` and required semantic MCP lifecycle tools

**Files:**
- Create: `backend/app/services/supervisor.py`
- Modify: `backend/app/context.py`
- Modify: `backend/app/mcp/__init__.py`
- Create: `backend/tests/test_supervisor_client.py`
- Create: `backend/tests/test_wp21_system_mcp.py`

**Interfaces:**
- `SupervisorClient.status() -> dict`
- `SupervisorClient.restart(component: Literal["api","web","mcp_tunnel","all"], actor="mcp", correlation_id=None) -> dict`
- AppContext gains `supervisor: SupervisorClient`.
- MCP tools present in every MCP mode: `sceneworks.system.status` (read-only) and `sceneworks.system.restart` (mutating, semantic enum only).

- [ ] **Step 1: Write failing client/MCP tests**

Tests use `httpx.MockTransport` or an injected transport to assert status decoding, token header on mutation, unavailable supervisor becomes a bounded MCP tool error, exact component enum only, and MCP schemas contain no path/PID/port/URL/command/environment fields.

- [ ] **Step 2: Verify RED**

Run from `backend/`:

```bash
uv run pytest tests/test_supervisor_client.py tests/test_wp21_system_mcp.py -q
```

Expected: missing `SupervisorClient`/system tools.

- [ ] **Step 3: Implement `SupervisorClient` and composition**

Default URL is `http://127.0.0.1:8020`; token comes from `SCENEWORKS_SUPERVISOR_TOKEN` or the local token file. Client has short bounded timeouts and never logs/returns the token.

- [ ] **Step 4: Add MCP tools in canonical `backend/app/mcp/__init__.py`**

Extend `tool_definitions()` and `call_tool()` so all modes can inspect status and request semantic restart. `restart` accepts only `api|web|mcp_tunnel|all`; `all` maps to supervisor `restart-all`. Return operation id/status only.

- [ ] **Step 5: Verify GREEN**

Run:

```bash
uv run pytest tests/test_supervisor_client.py tests/test_wp21_system_mcp.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/supervisor.py backend/app/context.py backend/app/mcp/__init__.py backend/tests/test_supervisor_client.py backend/tests/test_wp21_system_mcp.py

git commit -m "feat: expose semantic system lifecycle tools"
```

---

### Task 5: Server-only Next.js supervisor proxy and Diagnostics lifecycle UI

**Files:**
- Create: `web/lib/supervisor.ts`
- Create: `web/app/api/supervisor/status/route.ts`
- Create: `web/app/api/supervisor/operations/[id]/route.ts`
- Create: `web/app/api/supervisor/restart/route.ts`
- Modify: `web/app/diagnostics/page.tsx`
- Create: `web/tests/supervisor-helpers.test.mjs`
- Modify: `web/package.json`

**Interfaces:**
- Browser GET `/api/supervisor/status` -> server-side proxy to supervisor status.
- Browser GET `/api/supervisor/operations/{id}` -> bounded operation state.
- Browser POST `/api/supervisor/restart` body `{component:"api"|"web"|"mcp_tunnel"|"all"}` -> server-side authenticated supervisor mutation.
- `web/lib/supervisor.ts` contains server-only token/path resolution and supervisor fetch helpers; token is never serialized.

- [ ] **Step 1: Write failing helper test**

Add a Node built-in test script that verifies allowed-component validation and that public status normalization strips unknown/internal fields. Add `"test:unit": "node --test tests/*.test.mjs"`.

- [ ] **Step 2: Verify RED**

Run from `web/`:

```bash
npm run test:unit
```

Expected: FAIL because helper module/exports are missing.

- [ ] **Step 3: Implement server-only helpers and route handlers**

Use `import "server-only"` in token-bearing code. Resolve token from `SCENEWORKS_SUPERVISOR_TOKEN` or the local token file. Route handlers validate component enums before forwarding and return bounded 503/502 responses when supervisor is unavailable.

- [ ] **Step 4: Extend Diagnostics UI**

Add aggregate status and a `SceneWorks services` table with component state, last transition, recovery count/budget, per-component Restart, and `Restart SceneWorks`. Use `window.confirm` before mutation. Poll active operation; for restart-all tolerate fetch failures and retry after the web process returns. Disable relevant controls while active.

- [ ] **Step 5: Verify GREEN and production build**

Run:

```bash
npm run test:unit
npm run build
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add web

git commit -m "feat: add lifecycle controls to diagnostics"
```

---

### Task 6: CI, canonical docs, security invariants, and qualification gate

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `AGENTS.md`
- Modify: `README.md`
- Modify: `docs/architecture.md`
- Modify: `docs/architecture/control-plane-integrity.md`
- Modify: `docs/limitations.md`
- Modify: `docs/tutorials/chatgpt-mcp-plugin.md`
- Modify: `docs/tutorials/web-ui.md`
- Create: `docs/wp21-lifecycle-supervisor.md`

**Interfaces:**
- CI runs standard-library supervisor tests on Ubuntu and Windows, backend WP21 tests, frontend unit tests/build, existing backend suite, and existing deterministic qualification.
- Documentation states the exact local trust/security boundary and distinguishes automatic local recovery from future WP22 hub/edge remote recovery.

- [ ] **Step 1: Update CI before declaring completion**

Add a `supervisor` Ubuntu job running `python -m unittest discover -s supervisor/tests -v`; extend `windows-runtime` to run supervisor tests and PowerShell launcher parse; frontend job runs `npm run test:unit` before build. Keep all existing jobs/gates.

- [ ] **Step 2: Update canonical docs and invariants**

Add explicit invariant: SceneWorks infrastructure lifecycle is semantic and supervisor-owned; FastAPI/provider agents must not become lifecycle authority; automatic restart is bounded; port ownership alone cannot authorize kill; the supervisor is loopback-only and is not a general shell service.

- [ ] **Step 3: Run local deterministic verification where available**

Repository-root supervisor:

```bash
python -m unittest discover -s supervisor/tests -v
```

Backend:

```bash
cd backend
uv run pytest tests/test_supervisor_client.py tests/test_wp21_system_mcp.py -q
uv run pytest -m "not live"
uv run python -m evaluation --json qualification.json
```

Frontend:

```bash
cd web
npm run test:unit
npm run build
```

- [ ] **Step 4: Open PR to trigger GitHub Actions and inspect every CI job**

Create a PR from `feat/wp21-lifecycle-supervisor` to `master`. Require green supervisor, backend, Windows runtime, deterministic qualification, and frontend jobs before calling WP21 code-complete.

- [ ] **Step 5: Record the remaining host qualification boundary**

Do not fabricate real-laptop evidence from GitHub-hosted Windows runners. The real host qualification remains: tunnel kill/recovery, API kill/recovery, Diagnostics restart, restart-all UI reconnect, crash-loop DEGRADED behavior, and unrelated-port refusal on the user's Windows machine.

- [ ] **Step 6: Commit documentation/CI closure**

```bash
git add .github/workflows/ci.yml AGENTS.md README.md docs

git commit -m "docs: qualify WP21 lifecycle supervision"
```
