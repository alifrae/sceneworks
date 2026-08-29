# Quick Start

A short path from a clean checkout to useful SceneWorks work.

## Prerequisites

- Python 3.12+ and [uv](https://docs.astral.sh/uv/)
- Node.js 20+ with `npm`
- Git on PATH
- Optional autonomous worker: Gemini CLI (recommended/default), OpenCode, or OpenHands

## Start SceneWorks

### Windows launcher

From the repository root:

```powershell
.\scripts\start-sceneworks.cmd
```

Useful options:

```powershell
.\scripts\start-sceneworks.cmd -Dev
.\scripts\start-sceneworks.cmd -Rebuild
.\scripts\start-sceneworks.cmd -NoBrowser
.\scripts\start-sceneworks.cmd -NoTunnel
```

If a ChatGPT MCP tunnel is configured, the launcher can also start it. Keep tunnel credentials local and do not expose the bare FastAPI service publicly.

### Manual startup

Backend:

```bash
cd backend
uv sync
uv run python -m app.main
```

Frontend, in a second terminal:

```bash
cd web
npm install
npm run dev
```

Open `http://localhost:3000`. The API defaults to `http://127.0.0.1:8010`.

## 1. Register a repository

Open **Projects -> Add existing repository**.

Enter:

- a friendly project name;
- the absolute path to a local Git repository;
- optional description;
- optional test commands.

SceneWorks registers configuration only. It does not add SceneWorks files to the repository and does not modify your human working tree.

## 2. Create work

The quickest path is the composer on **Home**.

Describe the outcome, choose the project, and optionally choose:

- type: `task`, `bug`, `feature`, or `idea`;
- mode: `auto`, `change`, `investigate`, `plan`, or `ask`;
- attachments;
- an engineering contract with acceptance criteria, allowed scope and required tests.

Choose **Save to backlog** or **Start now**.

For lightweight defect/feature tracking, use **Issues**. Issues are normal SceneWorks Tasks, so diagnosis, implementation, Git provenance and verification stay attached to the same work item.

## 3. Follow governed work

Open the item under **Work**.

For a normal code change SceneWorks may run:

```text
triage / optional advisors
 -> Architect when required
 -> Engineer
 -> Reviewer
 -> human acceptance/rejection
```

Repository-grounded roles operate on commit-pinned isolated worktrees. Uncommitted edits in your human checkout are not part of the pinned snapshot.

The current task tabs are:

- **Plan**
- **Changes**
- **Results**
- **Activity**
- **Advanced**

A dedicated criterion-level **Verification** tab is not implemented yet; see [Verification and Issue Traceability](verification-and-issue-traceability.md). Reviewer approval should not be confused with objective verification.

## 4. Inspect operational state

Open **Control** to see a bounded operational view of:

- active EngineeringSessions;
- managed PCS runs;
- recent SceneWorks evidence;
- work/issue counts.

Control is observational. Engineering mutations continue through governed task actions or MCP/EngineeringSession tools.

## 5. Configure workers and ChatGPT/MCP

Open **Settings**.

You can:

- select the default autonomous worker;
- inspect backend health/version/detail;
- map `strongest`, `coding`, and `research` profiles to a backend/model;
- configure Gemini/OpenCode operational values;
- enable SceneWorks MCP and choose Observe/Standard/Advanced mode;
- set the Advanced-session permission ceiling.

Current routing limitation: role -> profile assignment is still code configuration in `backend/app/roles/definitions.py`; only profile -> backend/model is editable today.

## 6. Direct engineering control

In Advanced MCP mode, ChatGPT can supervise a provider-neutral `EngineeringSession` using SceneWorks-owned tools for:

- workspace read/search/write;
- commands and persistent processes;
- Git status/diff/commit;
- semantic PCS lifecycle/log/state/verification;
- managed PCS screenshot evidence;
- controlled PCS UI Automation fallback;
- optional delegation to an autonomous worker.

Gemini is therefore optional labor for direct engineering control, not the execution gateway.

See [ChatGPT MCP Plugin](tutorials/chatgpt-mcp-plugin.md) for the full flow.

## 7. Accepting work

When work reaches the human decision boundary:

1. inspect the diff and result commit;
2. inspect Reviewer notes;
3. inspect available objective evidence;
4. accept, reject, or send back.

**Accept does not merge into your main branch.** SceneWorks preserves the isolated branch/commit and the human decides how to integrate it.

For an issue-type work item, the recommended next traceability improvement is a durable Resolution snapshot containing root-cause claim, Git-derived fix/commit/files, verification evidence, and remaining risk. This is documented but not yet implemented.

## Next steps

- [Web UI Tutorial](tutorials/web-ui.md)
- [ChatGPT MCP Plugin](tutorials/chatgpt-mcp-plugin.md)
- [Architecture](architecture.md)
- [Agent Backends and Model Routing](backends.md)
- [Verification and Issue Traceability](verification-and-issue-traceability.md)
- [Known Limitations](limitations.md)