# Quick Start

First 10 minutes guide from zero to running SceneWorks.

## Prerequisites

- **Python 3.12+** and **[uv](https://docs.astral.sh/uv/)**
- **Node.js >= 20** with `npm`
- **Git** (on PATH)

## Start SceneWorks

### Windows launcher (recommended)

From the repository root:

```powershell
.\scripts\start-sceneworks.cmd
```

The launcher starts the backend, waits for it to become healthy, starts the
frontend, optionally starts the Secure MCP tunnel, and opens the web UI. It is
idempotent: already-running services are reused instead of duplicated.

Useful options:

```powershell
.\scripts\start-sceneworks.cmd -Dev
.\scripts\start-sceneworks.cmd -Rebuild
.\scripts\start-sceneworks.cmd -NoBrowser
.\scripts\start-sceneworks.cmd -NoTunnel
.\scripts\start-sceneworks.cmd -TunnelClientPath C:\path\to\tunnel-client-runtime-cloudflared.exe
```

For the ChatGPT MCP tunnel, the launcher expects the tunnel client at:

```text
tools\tunnel-client-runtime-cloudflared.exe
```

Override that location with `-TunnelClientPath` or the
`SCENEWORKS_TUNNEL_CLIENT_PATH` environment variable. The tunnel starts only
when `CONTROL_PLANE_TUNNEL_ID` and `CONTROL_PLANE_API_KEY` are present. The
local MCP target defaults to `http://127.0.0.1:8010/mcp` and can be overridden
with `MCP_SERVER_URL` or `-McpServerUrl`.

Do not commit tunnel credentials or the tunnel executable. The executable is
ignored by Git and is intentionally a local tool.

### Manual startup

Terminal 1 — backend:

```bash
cd backend
uv sync
uv run uvicorn app.main:app --reload
```

The API is at **http://127.0.0.1:8010** (docs at `/docs`).

Terminal 2 — frontend:

```bash
cd web
npm install
npm run dev
```

Open **http://localhost:3000**.

## First example

1. **Register a repository** — go to Projects, click Add Repository,
   enter an absolute path to a local Git repo.

2. **Create a task** — go to Tasks, click New Task, for example:

   > Add a `multiply(a, b)` function to `calc.py`, add tests, and preserve
   > all existing behavior.

3. **Start the workflow** — click "Start Architecture" on the task detail page.

4. **Observe automatic role routing** — the triage node analyzes the task,
   then the Architect inspects your codebase (read-only).

   > **Agents read committed state, not your working tree.** At triage,
   > SceneWorks resolves the head of the project's default branch and pins the
   > whole workflow to that commit; every role reads a worktree checked out at
   > it. Uncommitted or staged edits in your checkout are invisible to the
   > agents, and nothing they do can modify your checkout. **Commit work you
   > want the analysis to consider.** The pinned commit is shown as the task's
   > base commit.

5. **Review the architecture proposal** — read the analysis on the task page.

6. **Approve it** — click "Approve Architecture". The workflow runs
   Engineer -> Reviewer automatically.

7. **Watch Engineer execution** — files are edited in an isolated worktree
   (`sw-task-<id>`). You can follow live events in the Event Log.

8. **Watch Reviewer validation** — the Reviewer inspects the diff, runs tests,
   and either approves or requests changes.

9. **Inspect the result** — view the diff, tests, implementation summary,
   and review verdict on the task detail page.

10. **Accept/reject/send back** — Accept marks the task done but **does not
    merge into your human branch**. You decide what to integrate.

## Ask a company role

Go to Company, pick a role (CEO, CTO, Product, GTM, Technical Expert or
Architect), connect to a project, and ask a question. The answer is stored as
a company artifact.

When you attach a project, the ask is repository-grounded: it runs against a
commit-pinned snapshot of that repository — never your working tree — and the
stored decision records the commit that was analyzed. Asks with no project
attached are conversational and read no repository at all.

## Inspect Project Memory

Projects page -> click a project -> Memory tab.
Create architecture decisions, technology choices, product decisions,
initiative summaries, or constraints. Edit, archive, or supersede them.
Memory items are automatically injected into relevant workflow nodes.

## Choose Gemini ACP vs OpenHands

- **Gemini ACP** (default, validated): Ensure `gemini` is on PATH or set
  `SCENEWORKS_GEMINI_EXECUTABLE` in `backend/.env`.
  The model is selected by the Gemini CLI (`auto` by default). Override
  with `SCENEWORKS_GEMINI_MODEL` if needed.
- **OpenHands** (experimental): Set `SCENEWORKS_OPENHANDS_URL` in
  `backend/.env` pointing to a running OpenHands Agent Server.

Check Settings page to verify backend health.

## Next steps

- [ChatGPT MCP Plugin](tutorials/chatgpt-mcp-plugin.md) — connect ChatGPT to SceneWorks
- [Web UI Tutorial](tutorials/web-ui.md) — detailed step-by-step
- [Architecture](architecture.md) — component responsibilities
- [Development](development.md) — developer setup
