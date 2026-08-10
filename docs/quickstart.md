# Quick Start

First 10 minutes guide from zero to running SceneWorks.

## Prerequisites

- **Python 3.12+** and **[uv](https://docs.astral.sh/uv/)**
- **Node.js >= 20** with `npm`
- **Git** (on PATH)

## Start SceneWorks

### Terminal 1 — Backend

```bash
cd backend
uv sync
uv run uvicorn app.main:app --reload
```

The API is at **http://127.0.0.1:8010** (docs at `/docs`).

### Terminal 2 — Frontend

```bash
cd web
npm install
npm run dev
```

Open **http://localhost:3000**

## First example

1. **Register a repository** — go to Projects, click Add Repository,
   enter an absolute path to a local Git repo.

2. **Create a task** — go to Tasks, click New Task, for example:

   > Add a `multiply(a, b)` function to `calc.py`, add tests, and preserve
   > all existing behavior.

3. **Start the workflow** — click "Start Architecture" on the task detail page.

4. **Observe automatic role routing** — the triage node analyzes the task,
   then the Architect inspects your codebase (read-only).

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

Go to Company, pick a role (CTO, Product, etc.), connect to a project,
and ask a question. The answer is stored as a company artifact.

## Inspect Project Memory (V2.4)

Projects page -> click a project -> Memory tab.
Create architecture decisions, technology choices, product decisions,
initiative summaries, or constraints. Edit, archive, or supersede them.
Memory items are automatically injected into relevant workflow nodes.

## Choose Gemini ACP vs OpenHands

- **Gemini ACP** (default): Set `SCENEWORKS_GEMINI_EXECUTABLE` in `backend/.env`
  or ensure `gemini` is on PATH.
- **OpenHands** (optional): Set `SCENEWORKS_OPENHANDS_URL` in `backend/.env`
  pointing to a running OpenHands Agent Server.

Check Settings page to verify backend health.

## Next steps

- [Web UI Tutorial](tutorials/web-ui.md) — detailed step-by-step
- [Architecture](architecture.md) — component responsibilities
- [Development](development.md) — developer setup
