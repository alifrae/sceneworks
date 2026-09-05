# SceneWorks V4.0.0

SceneWorks is a local engineering control plane for governed software work. It combines a conventional task workflow with provider-neutral engineering sessions, durable evidence, semantic PCS control, supervised local infrastructure lifecycle, and optional autonomous coding workers.

The current architecture deliberately separates three things that older documentation often conflated:

```text
Supervisor / operator
ChatGPT, web UI, or human
        |
        v
SceneWorks control plane
Tasks, EngineeringSessions, permissions, evidence, verification
        |
        +---------------------------+
        |                           |
        v                           v
SceneWorks NativeRuntime        AgentBackend
files / commands / process      Gemini ACP / OpenCode / OpenHands
Git / PCS / GUI evidence        optional autonomous worker

Local lifecycle supervisor (separate process)
API / web / MCP tunnel ownership, health, bounded recovery, journal
```

Gemini CLI remains the recommended default autonomous worker, but direct MCP engineering control does not depend on Gemini authentication or model availability. API/web/MCP-tunnel lifecycle is owned by the separate loopback SceneWorks Supervisor, not by provider agents or FastAPI itself.

## Current product surfaces

- **Home** — create work and see items that need attention, active work, and recent results.
- **Work** — governed tasks using `task | bug | feature | idea` work-item types and `auto | change | investigate | plan | ask` execution intent.
- **Issues** — a lightweight engineering issue view over the existing task model; there is no second Jira-like ticket database.
- **Projects** — register/unregister local repositories and retain registration history.
- **Control** — read-only operational view of EngineeringSessions, managed PCS processes, and recent evidence.
- **Settings** — backend health, default worker, model-profile routing, MCP mode and Advanced permission ceiling.
- **Diagnostics** — API/performance/connectivity diagnostics plus bounded API/web/MCP-tunnel lifecycle state and semantic restart controls.

## Execution architecture

SceneWorks supports two complementary execution paths.

### Governed workflow

A task may run through the LangGraph workflow:

```text
Triage / advisory roles
        -> Architect
        -> human architecture decision when required
        -> Engineer
        -> Reviewer
        -> human acceptance/rejection
```

The workflow uses explicit task states, isolated Git worktrees, persistent `Execution` rows, structured events, cancellation/recovery rules, and no automatic merge into the human branch.

### Direct engineering control

Advanced MCP exposes a provider-neutral `EngineeringSession` with a SceneWorks-owned execution runtime:

```text
EngineeringSession
  -> workspace read/search/write
  -> command.run
  -> process start/output/stop
  -> Git status/diff/commit
  -> semantic PCS lifecycle/log/state/verification
  -> managed PCS screenshot evidence
  -> controlled Windows UI Automation fallback
  -> optional agent delegation
```

Direct engineering actions are captured in the durable WP15 evidence ledger. Agent/model conclusions are inference; SceneWorks-observed file/Git/command/process/PCS/GUI observations are evidence.

Infrastructure lifecycle is a separate bounded surface: MCP exposes only `sceneworks.system.status` and semantic `sceneworks.system.restart(api|web|mcp_tunnel|all)`. It does not expose PID, port, executable, path, environment, command, or shell authority.

## PCS control

For projects with PCS runtime-control configuration, SceneWorks can:

- start/stop/restart a named PCS profile;
- collect durable stdout/stderr evidence;
- inspect health and explicit runtime state;
- access governed external recordings through read-only aliases;
- execute deterministic verification runbooks;
- discover managed PCS windows/dialogs;
- capture and compare screenshot evidence;
- use controlled UI Automation actions when no semantic PCS API exists.

GUI automation is a fallback. Deterministic PCS/API control remains preferred.

## Local infrastructure supervision

WP21 adds an out-of-process standard-library Python supervisor bound only to `127.0.0.1:8020`. It owns SceneWorks API, web, and MCP-tunnel process lifecycle, health sampling, bounded automatic recovery, process-ownership checks, and the durable operation journal.

The Windows launcher is bootstrap/provisioning glue and a supervisor client; it no longer terminates listeners directly. The supervisor refuses destructive actions when process ownership is ambiguous. See [WP21 Lifecycle Supervisor](docs/wp21-lifecycle-supervisor.md) and the [Windows launcher guide](docs/development/windows-launcher.md).

## Model routing

Roles declare provider-neutral model intent such as `strongest`, `coding`, or `research`. Settings maps each profile to an optional backend/model. The concrete backend/model is resolved when an `Execution` is created and persisted for provenance.

Current limitation: the **role -> model-profile assignment itself remains defined in `backend/app/roles/definitions.py` and is not yet editable from Settings**. See [Verification and issue traceability](docs/verification-and-issue-traceability.md) for the remaining routing/verification UX gap.

## Setup

Requirements: Python 3.12+, Node.js 20+, Git, and [uv](https://docs.astral.sh/uv/).

### Backend

```bash
cd backend
uv sync
uv run python -m app.main
```

Default API: `http://127.0.0.1:8010`

### Frontend

```bash
cd web
npm install
npm run dev
```

Default UI: `http://localhost:3000`

The frontend API URL can be overridden with `NEXT_PUBLIC_API_URL`.

For normal Windows operation, use `scripts/start-sceneworks.ps1`; it starts/reuses the lifecycle supervisor and requests semantic starts for the SceneWorks services.

## Register a repository

Open **Projects -> Add existing repository** and enter an absolute path to a local Git repository. SceneWorks validates the repository and stores configuration; it does not add SceneWorks code to the repository.

Repository-changing agent/workflow operations happen in isolated worktrees under the configured SceneWorks worktree root. Unregister removes SceneWorks-owned records/configuration only; it never deletes the repository or configured external PCS assets. Active EngineeringSessions or managed PCS processes must be closed/stopped first.

## Testing

The normal qualification path is:

```bash
python -m unittest discover -s supervisor/tests -v

cd backend
uv sync --frozen
uv run pytest -m "not live"

cd ../web
npm ci
npm run test:unit
npm run build
```

CI also parses the PowerShell launcher and runs the supervisor suite on Windows. The repository contains deterministic provider-independent qualification scenarios. Live provider/Windows host failure-injection qualification is intentionally separate from the normal non-live suite.

## Documentation

- [Architecture](docs/architecture.md) — current component and authority boundaries.
- [Agent backends](docs/backends.md) — provider/runtime separation and routing.
- [Known limitations](docs/limitations.md) — current, not historical, limitations.
- [Verification and issue traceability](docs/verification-and-issue-traceability.md) — current verification UX gap and recommended issue-resolution contract.
- [ChatGPT/MCP tutorial](docs/tutorials/chatgpt-mcp-plugin.md) — direct engineering and PCS control.
- [WP21 Lifecycle Supervisor](docs/wp21-lifecycle-supervisor.md) — current local infrastructure lifecycle and recovery contract.
- [WP14 provider-neutral execution](docs/wp14-provider-neutral-execution.md) through [WP21 lifecycle supervisor](docs/wp21-lifecycle-supervisor.md) — historical implementation records and WP-specific decisions.

Historical WP/version documents describe the state at the time they were written. The files above are the canonical source for current architecture and limitations.
