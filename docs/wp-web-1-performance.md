# WP-WEB-1 — Web Responsiveness Audit and Fix

Status: implemented and verified on 2026-08-12.

This report records the audit evidence and the changes made in the existing
Next.js/FastAPI architecture. Timings below are local reference measurements,
not synthetic sleeps or production SLOs.

## Root-cause report

### Frontend and server-state causes

| Observed behavior | Evidence / measured latency | Root cause | Fix | Result |
|---|---|---|---|---|
| Task action buttons appeared blocked until the POST and a second task GET completed. | Source trace: `ActionBar` awaited `onAction`; task detail awaited the mutation and then `refresh()`. Chromium contract test measured click → visible `Starting…` at **95–98 ms** across the final two runs while the POST was held open. | Local state was updated only after remote work. | `TaskDetailPage` sets `busy`/`pendingAction` before the request, renders an action-specific progress label, and reconciles directly from the authoritative mutation response. | Immediate feedback is independent of agent duration; failure restores the original control. |
| An acknowledged task action could visibly revert to its old status. | Regression reproduced in the held-request test: the post-response `ARCHITECTURE_ANALYSIS` state was overwritten by a stale `NEW` GET. | The status-dependent polling effect called an initial refresh again whenever status changed. | Initial load and fallback polling are separate effects. The authoritative mutation response is no longer immediately clobbered. | Contract now passes. |
| Navigation looked unchanged while a destination API was pending. | Existing pages returned a blank/loading message only after their client component mounted; sidebar had no transition state. | No visible route-transition shell. | Sidebar shows an immediate “Opening…” state; data-dependent pages render `LoadingShell`; active route data remains independently loaded. | Navigation shell contract passes with dashboard data held open. |
| Repeated visits and mounted pages duplicated GETs. | Multiple pages used independent `setInterval` refreshes; no cache or in-flight deduplication existed. | Server state was fetched directly in each component. | `web/lib/api.ts` now provides a short 2-second GET snapshot cache and in-flight request deduplication, with targeted mutation invalidation. | Repeated unchanged GETs are coalesced without adding a second state framework. |
| Slow operations refreshed broad views after every mutation. | Task actions refreshed the complete task detail; create flows refetched lists; no scoped invalidation existed. | Refresh was the only reconciliation mechanism. | Create mutations insert the acknowledged row locally; task/project updates use the returned row; cache invalidation is scoped by resource. | Unrelated dashboard/projects/executions/events queries are not refetched for task actions. |
| Event/log updates re-rendered the whole history and accepted large response windows. | Existing SSE handler appended to an array and rendered every item; replay endpoints returned up to 1,000 rows; client retained 800 without memoized rows. | Unbounded-ish history was represented as one reactive list. | REST replay is capped at 800; SSE/live history is merged by event id and capped at 800; `EventEntry` is memoized; compact views remain capped at 120; event callbacks refresh only authoritative task/execution transitions. | 2,000 mocked events produce exactly 800 DOM entries; streaming contract passes. |
| Task detail repeatedly fetched the diff while an agent was running. | `taskDiff` was requested from every 4-second task refresh whenever a worktree existed. | Diff identity was not tracked. | Diff fetch is keyed to worktree path/result commit and only runs when that key changes. | Repeated status updates do not re-fetch unchanged diff data. |
| Manual company asks could hold the browser while creating a repository snapshot and building context. | `CompanyService.ask` created the snapshot and built the prompt before returning the execution. | A long-running preparation step was inside the HTTP acknowledgement path. | The execution row is created and returned as `QUEUED`; snapshot/prompt preparation and engine start run in a background task. Preparation failure is finalized as a failed execution with an event. | Manual asks now use acknowledgement-oriented semantics while retaining commit-pinned workspaces. |
| The UI sent a valid-looking action name that FastAPI did not recognize. | Browser run returned `unknown action: start_architecture`; backend routes are hyphenated (`start-architecture`). | Frontend enum names and API path names diverged. | `api.taskAction` converts underscore action names to the backend’s hyphenated path at the boundary. | Existing workflow actions reconcile correctly; full workflow API tests remain green. |

### API/database/backend causes

| Observed behavior | Evidence / measured latency | Root cause | Fix / result |
|---|---|---|---|
| Task list cost grew with the number of rows. | `list_tasks` loaded the list, then `_task_out` opened another session and queried the project and current execution for every task. On the current local dataset, the old running process returned `/api/tasks?limit=200` at a **195 ms client median**; the isolated fixed process measured **122 ms client / 11.2 ms server median** over five samples. | N+1 project/execution lookups. | Task list now uses the eager-loaded project plus one batched current-execution status query. |
| Project list also scaled with one active-count query per project. | Old process `/api/projects` was **142 ms client median**; fixed isolated process was **57 ms client / 7.9 ms server median** over five samples. | `_to_out` performed a count per project. | Project list now batches active counts and returns a bounded list. |
| Large list and event responses had no explicit UI-facing bound. | Tasks/projects had no list limit; event routes hard-coded 1,000. | API returned all history/list rows by default. | Tasks/projects default to 200 and cap at 500; event replay defaults to 500 and caps at 800; executions UI requests 100. |
| Workflow action acknowledgement could include graph/checkpoint setup. | `start_workflow`, approval, implementation, review, and send-back called `_get_compiled_graph()` before creating the background graph task. | Graph compilation/checkpointer setup sat on the mutation path. | All workflow entry points transition/emit, then schedule `_launch_graph`; compilation happens in the background. |
| Backend timing was not correlated with browser requests. | No request ID or server timing headers existed. | Frontend and FastAPI timings could not be joined. | Browser sends `X-Request-ID`; development diagnostics record endpoint, duration, cause, server duration, and response size; FastAPI returns `X-Request-ID` and `X-Process-Time-Ms`. |

The current isolated instrumented process measured these reference medians over
five requests on the existing local database:

| Endpoint | Old process client median | Fixed process client median | Fixed server median |
|---|---:|---:|---:|
| `/api/dashboard` | 82 ms | 77 ms | 14.3 ms |
| `/api/projects` | 142 ms | 57 ms | 7.9 ms |
| `/api/tasks?limit=200` | 195 ms | 122 ms | 11.2 ms |
| `/api/executions?limit=100` | 268 ms | 306 ms | 11.5 ms |

The old process was the pre-change application already listening on port 8010;
the fixed process was started separately on port 8011 with the same workspace
and database. These measurements are directional because they are separate
processes and a warm local dataset. Execution payloads are large (about 215 KB
in this dataset), so serialization/network transfer remains the dominant
client-side cost there.

## Representative interaction traces

### Start architecture

Before:

```text
click
→ no local state change
→ POST /api/tasks/{id}/actions/start_architecture
→ wait for graph/checkpointer setup
→ task GET refresh
→ render
```

After:

```text
click
→ local button state: Starting… (95–98 ms measured in Chromium)
→ POST /api/tasks/{id}/actions/start-architecture
→ task transition response reconciles the detail view
→ graph compilation/agent work continues asynchronously
→ SSE task/workflow events drive authoritative refreshes
```

### Manual company ask

Before:

```text
click
→ validate project
→ create detached snapshot worktree
→ build prompt/context
→ create execution
→ start engine
→ response
```

After:

```text
click
→ local Starting… feedback
→ create QUEUED execution and acknowledge
→ background snapshot/prompt preparation
→ start engine
→ execution events and artifact polling update the view
```

### Event history

Before:

```text
replay up to 1,000 rows + SSE
→ every new event creates a new full history render
```

After:

```text
bounded replay/live merge by event id (max 800)
→ new event appends incrementally
→ memoized existing rows are retained
→ only authoritative task/execution transitions trigger detail refresh
```

## Architecture and dependency decisions

### Changes

- Query/cache ownership remains in `web/lib/api.ts`; no global state framework
  was introduced. Cache entries are short-lived and mutations invalidate only
  related resource prefixes.
- Optimistic/pending state is limited to safe UI acknowledgement: buttons,
  cancellation state, created rows, and navigation feedback. Final state comes
  from the backend response or event stream; failed mutations restore controls
  and show the server error.
- SSE remains the live event source. REST is used for bounded initial replay;
  event IDs deduplicate the two paths.
- Workflow and company API semantics are acknowledgement-oriented; long-running
  graph/snapshot preparation no longer occupies the browser request.
- List bounds and event windows prevent uncontrolled DOM growth. Full list
  virtualization is not yet needed at the current 100–800 row windows.
- Markdown rendering is memoized; event rows are memoized and capped.

### Dependency review

| Dependency/capability | Decision | Reason |
|---|---|---|
| Next.js 15 / React 19 / App Router | KEEP | Existing architecture is sufficient; changes are component/effect scoped. |
| Native `fetch` client | ADAPT | Added cache, in-flight dedupe, request IDs, timings, and scoped invalidation. |
| React Context/global state | KEEP ABSENT | No evidence justified introducing a new global state layer. |
| TanStack Query or equivalent | KEEP ABSENT / ADAPT CURRENT | A small resource cache covers the measured problem without a second server-state owner. |
| Native `EventSource` / FastAPI SSE | KEEP / ADAPT | Existing infrastructure is appropriate; replay and rendering were bounded. |
| Markdown renderer | KEEP / ADAPT | Existing minimal renderer is retained and memoized; no material dependency evidence justified replacement. |
| Virtualization library | KEEP ABSENT | API/UI windows now bound history/list DOM; revisit if real datasets exceed these windows. |
| Playwright / TypeScript / Next build tooling | KEEP | Added focused browser contracts; no framework migration needed. |

## Regression coverage

Added `web/e2e/responsiveness.spec.ts`:

- immediate action feedback before a held backend response, including the
  measured hard budget `<200 ms`;
- destination loading shell visible while dashboard data is held;
- 2,000-event input produces a bounded 800-entry DOM window;
- failed action acknowledgement restores the control and surfaces useful error
  state.

Also tightened the pre-existing company smoke selector to use exact role-card
text instead of a strict-mode-ambiguous substring locator.

Verification completed:

- `npx tsc --noEmit`: passed;
- `npm run build`: passed;
- `npx playwright test e2e/responsiveness.spec.ts`: 4 passed;
- existing company browser smoke: passed;
- `PYTHONPATH=. uv run pytest -q`: **132 passed** in 4m55s;
- full Playwright run: **16 passed** in 2m12s, including the workflow/API/browser
  tests and all 4 responsiveness contracts.

## Remaining bottlenecks / deferred work

- The Company page still polls artifacts every 5 seconds because artifact
  creation is not currently delivered through a dedicated filtered UI stream.
- Dashboard/backend health still refreshes on a 10-second fallback interval;
  health is independent of dashboard data but does not yet have a dedicated
  event channel.
- Execution rows are bounded to 100 in the UI, but a 215 KB local response
  still makes payload size/serialization noticeable. Cursor pagination or a
  compact execution-list schema is a follow-up optimization.
- The app still uses a single-process in-memory event bus; multi-process event
  delivery and durable cursor replay are outside this work package.
- Backend agent adapters may use their own provider polling (notably the
  OpenHands compatibility path). That is worker-side behavior, not browser
  shell blocking, and remains deferred.
- No new full virtualization dependency was added. If production history
  exceeds the explicit windows, the next step is cursor pagination or a
  virtualization library backed by measurements.
