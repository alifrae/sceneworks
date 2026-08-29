# Web UI Tutorial

This guide covers the current WP20 web application. For direct ChatGPT/MCP engineering control, use the separate [ChatGPT MCP tutorial](chatgpt-mcp-plugin.md).

## 1. Start SceneWorks

Requirements:

- Python 3.12+ with `uv`
- Node.js 20+ with `npm`
- Git

Backend:

```bash
cd backend
uv sync
uv run python -m app.main
```

Frontend, in another terminal:

```bash
cd web
npm install
npm run dev
```

Open `http://localhost:3000`.

## 2. Understand the navigation

The primary navigation is intentionally small:

- **Home** — create work and see attention/active/recent items.
- **Work** — all governed SceneWorks work items.
- **Issues** — lightweight bug/feature/idea tracking over the same Task records.
- **Projects** — registered repositories and project actions.
- **Control** — active EngineeringSessions, managed PCS runs and recent evidence.
- **Settings** — backend/model/MCP configuration.

Diagnostics and other operational pages remain secondary surfaces.

## 3. Register a project

Open **Projects -> Add existing repository** and enter:

- name;
- absolute repository path;
- optional description;
- optional test commands.

Successful registrations are remembered in browser registration history so familiar repositories can be reused without retyping every field.

The repository remains yours. SceneWorks does not add project files or modify the human checkout simply by registering it.

### Unregistering

Each project card contains **Unregister from SceneWorks** in the same project action area.

Unregister removes SceneWorks-owned records/configuration when safe. It never deletes the Git repository or external PCS assets. If a live EngineeringSession or managed PCS process exists, SceneWorks refuses deletion until that authority/process is closed or stopped.

## 4. Create a work item

Use the composer on **Home**.

Enter the request and choose a project. Optional controls include:

- type: `task | bug | feature | idea`;
- mode: `auto | change | investigate | plan | ask`;
- attachments;
- engineering-contract fields such as acceptance criteria, allowed scope, forbidden changes and required tests.

Choose:

- **Save to backlog** — create the item without starting workflow execution;
- **Start now** — create it and begin the governed workflow.

## 5. Use Issues without turning SceneWorks into Jira

The **Issues** page is a focused view over existing Tasks whose type is `bug`, `feature`, or `idea`.

You can:

- capture a new bug/feature/idea quickly;
- filter Open/Closed/All;
- change type/priority while the item is still editable;
- open the same item in the normal Work thread when investigation or implementation begins.

There is no separate issue database, sprint board, story-point model or duplicate lifecycle. The same task ID owns the request, implementation, Git provenance and later evidence.

A structured issue Resolution snapshot (root cause, fix, verification, remaining risk) is recommended but not implemented yet; see [Verification and Issue Traceability](../verification-and-issue-traceability.md).

## 6. Follow the governed workflow

For code-changing work, SceneWorks may route through:

```text
Triage / optional advisors
 -> Architect when required
 -> human architecture decision when required
 -> Engineer
 -> Reviewer
 -> human acceptance/rejection
```

The exact path depends on execution intent and deterministic routing rules. Bounded lower-risk bugs can skip redundant architecture when the required contract fields are present.

Agent repository access uses commit-pinned isolated worktrees. The human checkout is not the agent workspace.

## 7. Read a Work thread

A Work item combines the conversation/decision boundary with detailed technical views.

Current tabs:

### Plan

Architecture/advisory output when available.

### Changes

Base/result commit information and the implementation diff.

### Results

Implementation summary and Reviewer verdict/notes.

### Activity

Structured task/execution events.

### Advanced

Raw operational identifiers/execution details useful for debugging.

### Verification status

A dedicated **Verification** tab does **not** exist yet. SceneWorks already stores acceptance criteria, required tests, Git provenance and engineering evidence, but the criterion-level `PASS | FAIL | UNVERIFIABLE` synthesis is a remaining product gap.

Do not interpret a visible Reviewer `APPROVED` verdict as equivalent to every acceptance criterion being objectively verified.

## 8. Human decisions

When an item reaches a human boundary, the page presents only valid actions for that state. Depending on the workflow this can include:

- approve/reject/request architecture revision;
- accept/reject final work;
- send back to Engineer;
- cancel/retry.

Accepting a task marks the SceneWorks work item accepted. **It does not merge the task branch into your main branch.**

## 9. Control page

Open **Control** for current operational truth rather than workflow prose.

It shows a bounded read-only projection of:

- active EngineeringSessions;
- session runtime/branch/task correlation;
- managed PCS processes/profile/status/PID;
- recent evidence operation/category/status;
- counts for active work, attention items and issues.

The page deliberately does not expose raw evidence payloads or host worktree paths and does not introduce a second mutation API.

## 10. Settings

Settings contains three distinct concepts.

### Autonomous agent backends

You can select the default worker and inspect availability/version/detail for configured backends.

Current choices include:

- `gemini_acp` — recommended/default autonomous worker;
- `opencode` — non-ACP backup for write-capable coding/delegation;
- `openhands` — optional/experimental;
- `fake` — tests only.

### Model-profile routing

Roles request provider-neutral profiles such as `strongest`, `coding`, and `research`. Settings can map each profile to a backend and optional concrete model.

The concrete target is persisted on each Execution for provenance.

Current limitation: role -> profile assignment is still code configuration. For example Engineer defaults to `coding`, while Architect and Reviewer default to `strongest`. This edge is not editable in Settings yet.

### ChatGPT / MCP

MCP can run in:

- **Observe** — read-only semantic/project/evidence access;
- **Standard** — governed SceneWorks task/workflow actions;
- **Advanced** — direct EngineeringSession control plus the configured permission ceiling.

Advanced mode can expose SceneWorks-owned workspace/command/process/Git/PCS/GUI capabilities. It is powerful but is not an OS sandbox.

## 11. PCS-specific control

When a project has PCS run profiles configured, direct SceneWorks control can provide:

- start/stop/restart/status;
- durable logs/errors;
- health and explicit runtime state;
- governed external recording aliases;
- deterministic verification runbooks;
- managed window/dialog discovery;
- screenshot/visual comparison evidence;
- controlled Windows UI Automation fallback.

These are primarily MCP/engineering-control capabilities; the WP20 Control page observes their state rather than duplicating all mutations in the browser.

## 12. Troubleshooting

### API offline

The UI reports when `http://127.0.0.1:8010` cannot be reached. Confirm the backend is running and that `NEXT_PUBLIC_API_URL` matches any custom port.

### Backend unavailable

Open **Settings** and inspect backend health. A failed Gemini/OpenCode/OpenHands backend does not remove SceneWorks' direct NativeRuntime capabilities, but governed autonomous workflow steps requiring that backend cannot run until a usable worker is selected.

### Project cannot be unregistered

Stop any managed PCS run and close active EngineeringSessions for that project first. SceneWorks deliberately refuses to delete the authority record of a live process/session.

### Task appears stuck

Check:

- Work -> Activity;
- Control for session/PCS state;
- Diagnostics for API problems;
- backend terminal logs.

### Windows GUI automation unavailable

WP18 uses Windows UI Automation patterns exposed by the managed PCS application. If a Qt/control does not expose a usable accessibility pattern, SceneWorks fails explicitly; it does not silently fall back to coordinate clicking.

## 13. What to look at next

- [Quick Start](../quickstart.md)
- [Architecture](../architecture.md)
- [Agent Backends and Model Routing](../backends.md)
- [Verification and Issue Traceability](../verification-and-issue-traceability.md)
- [Known Limitations](../limitations.md)
- [ChatGPT MCP tutorial](chatgpt-mcp-plugin.md)