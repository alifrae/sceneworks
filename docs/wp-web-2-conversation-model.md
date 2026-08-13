# WP-WEB-2 — Conversation-First Interaction Model

Status: implemented and verified on 2026-08-13.

This report documents the interaction-model audit, the design decisions, and
the verification evidence for WP-WEB-2. It assumes the WP-WEB-1 responsiveness
contracts (`docs/wp-web-1-performance.md`) as a starting baseline and keeps
them green throughout.

## A. Before/after information architecture

### Before (audited)

The homepage was the operational dashboard (`/`): four KPI counters
(active tasks, awaiting approval, running executions, failed executions),
a backend-health strip, a "recently completed tasks" table, a "failed
executions" table, and a row of buttons for company roles. There was no
request composer anywhere in the primary navigation. The sidebar listed
Dashboard, Projects, Tasks, Company, Executions, Settings — six items, all
named after backend resource types.

Tracing the twelve journeys named in the work package against that shell:

| # | Journey | Before-state path | Problems found |
|---|---|---|---|
| 1 | Open SceneWorks | `/` → KPI dashboard | No call to action. Nothing says "ask the team something." |
| 2 | Want the team to investigate | Sidebar → Tasks → **+ New task** | Requires knowing "Tasks" is where requests live; form asks for `title`/`description`/`priority`, not "what do you want." |
| 3 | Submit the request | `POST /api/tasks` returns `NEW`; no workflow started | The user lands back on the Tasks table. Nothing is running. A **second**, undiscoverable action (`Run Architect analysis` on the detail page) is required to actually start work. |
| 4 | Triage begins | invisible — `ARCHITECTURE_ANALYSIS` covers triage, advisory roles, and the architect indistinguishably | The task detail page's stepper shows one blob ("Architect") for what is actually up to four sequential agent roles. |
| 5 | Architect investigates | Task detail page, "Architecture analysis" result box, mixed in with Implementation/Review boxes once those exist | No sense of "who is working right now" separate from the raw `status` string. |
| 6 | Plan produced | Same result box, `AWAITING_ARCHITECTURE_APPROVAL` badge (raw enum, unformatted apart from `_` → space) | Plan is discoverable, but visually identical in weight to every other panel on the page. |
| 7 | Approval required | `ActionBar` renders every `allowed_action` the state machine reports, including internal-only actions like `architecture_completed` with no working route (dead buttons) | No dedicated "this needs you" surface; the founder has to already be on the task page to know. |
| 8 | Engineer implements | Same page, same layout, `IMPLEMENTING`/`TESTING` badges | Progress is a static 4-box "stepper" (Architect/Engineer/Reviewer/Human) that doesn't distinguish investigation from planning, or implementation from testing. |
| 9 | Reviewer evaluates | Same page, `REVIEWING` badge, review result box appears once done | Same as above. |
| 10 | Repair/retry | `CHANGES_REQUESTED`, engine auto-loops internally | Indistinguishable in the UI from "stuck, needs you" — no signal for whether a repair is in flight or the retry budget was exhausted. |
| 11 | Reaches `READY_FOR_HUMAN` | Badge changes; Accept/Reject/Send-back buttons appear in the same `ActionBar` as everything else | "Done" isn't a distinct visual state — the page looks the same as every other in-progress state. |
| 12 | Return later | Sidebar → Tasks (list of all tasks, plain table) → click into `/tasks/{id}` | The user must recall the task id/title; there is no "your recent conversations" concept; results (git commit, files changed, test outcome) are scattered across a result box, a diff panel, and an event log the user has to correlate manually. |

Duplicated information: task status appeared as a badge, as a stepper
label, and as raw text in the events log, worded differently in each
place. Internal concepts exposed without translation: `ARCHITECTURE_ANALYSIS`,
`AWAITING_ARCHITECTURE_APPROVAL`, `READY_FOR_HUMAN`, `current_execution_id`,
execution status strings (`QUEUED`/`STARTING`/`RUNNING`) — all shown verbatim.
Dead ends: `ActionBar` rendered actions with no backend route (e.g.
`architecture_completed`) because `TaskStateMachine.allowed_actions()` returns
every transition valid from a state, including system-only ones never wired
to an API action.

### After (this WP)

```
SceneWorks
├─ Home (/)              "Ask the team" composer + Needs your attention +
│                          Active work + Recent results
├─ Work (/work)           full list, filterable (attention/active/completed/all)
│  └─ Work Thread (/work/{id})  conversation, progress, team, Plan/Changes/
│                                Results/Activity/Advanced tabs
├─ Projects (/projects)   unchanged — repository registration/config
├─ Team (/company)        role roster + manual advisory asks (renamed from
│                          "Company"; content is the org chart + manual
│                          invocation panel that already existed)
├─ Dashboard (/dashboard) the old KPI/health view, relocated, still reachable
└─ Settings (/settings)   unchanged
```

Sidebar always shows **+ New request** (→ `/`) above the nav, plus a
"Recent" list of the last 6 updated Work items so reopening current work
never requires the list page. `/tasks`, `/tasks/{id}`, `/executions`,
`/executions/{id}` are kept exactly as they were — they are now the
**Advanced** layer, linked to from the Work Thread's Advanced tab, not
primary navigation.

## B. Interaction model

```
New request (Composer, "/")
  │  POST /api/tasks                       — acknowledged in one round trip
  │  POST /api/tasks/{id}/actions/start-architecture   — fired, not awaited
  ▼
Work Thread ("/work/{id}")
  │
  │  backend runs: triage → [product/cto/technical_expert] → architect
  ▼
Investigating / Architecture (human-facing stage, derived from
  status=ARCHITECTURE_ANALYSIS + current_role)
  │
  ▼
Needs your input: architecture plan (status=AWAITING_ARCHITECTURE_APPROVAL)
  │  DecisionCard: Approve plan / Reject plan / Request changes / Cancel
  │  → POST /api/tasks/{id}/actions/{approve|reject|request-revision}-architecture
  ▼
Implementing (status=IMPLEMENTING/TESTING, owner=Engineer)
  ▼
Reviewing (status=REVIEWING, owner=Reviewer)
  │  auto-repair loop possible (CHANGES_REQUESTED, owner=Engineer) — shown as
  │  "Engineer is working" with no decision buttons while an execution is
  │  actually running, matching backend reality instead of implying otherwise
  ▼
Needs your input: result ready (status=READY_FOR_HUMAN)
  │  DecisionCard: Accept result / Reject result / Send back to Engineer
  │  → POST /api/tasks/{id}/actions/{accept|reject|send-back}
  ▼
Completed (status=ACCEPTED/REJECTED) — Results tab: verdict, commit, files
  changed, "Ask a follow-up" (opens a new request in the same project)
```

Exceptional overlays that can occur at any non-terminal point: **Blocked**
(`CHANGES_REQUESTED` with no active execution — the repair-iteration budget
was reached) and **Failed** (`status=FAILED`, offers Retry / Retry from
architecture) and **Cancelled** (`status=CANCELLED`, terminal, no actions).

This maps onto the existing backend one-to-one: `Task` **is** the Work object
(Section 2 of the WP explicitly allowed reusing an existing entity instead of
inventing a new one — a Task already aggregates request, workflow status,
current role, current execution, and role results). No new backend entity
was introduced.

## C. State mapping (explicit — `web/lib/workStages.ts`)

| Backend `status` | `current_role` / execution | User-facing stage | Exceptional overlay | Meaningful actions offered |
|---|---|---|---|---|
| `NEW` | — | Submitted | — | *(none yet; workflow starts automatically on request submission)* |
| `ARCHITECTURE_ANALYSIS` | `triage`/`product`/`cto`/`technical_expert` | Investigating | — | Cancel |
| `ARCHITECTURE_ANALYSIS` | `architect` | Architecture | — | Cancel |
| `AWAITING_ARCHITECTURE_APPROVAL` | — | Architecture | **Needs your input** — "Architecture plan is waiting for your approval." | Approve plan, Reject plan, Request changes, Cancel |
| `READY_TO_IMPLEMENT` | — | Implementing | — | Resume implementation, Cancel |
| `IMPLEMENTING` / `TESTING` | `engineer` | Implementing | — | Cancel |
| `REVIEWING` | `reviewer` | Reviewing | — | Cancel |
| `CHANGES_REQUESTED` | `engineer`, execution running | Implementing | — | Cancel |
| `CHANGES_REQUESTED` | no active execution | Implementing | **Blocked** — "The reviewer requested changes and the automatic repair limit was reached." | Resume implementation, Cancel |
| `READY_FOR_HUMAN` | — | Completed | **Needs your input** — "The reviewer approved the work — accept it or send it back." (or "Findings are ready for you to review." for advisory-only requests) | Accept result, Reject result, Send back to Engineer |
| `ACCEPTED` | — | Completed (outcome: accepted) | — | *(none — terminal; "Ask a follow-up" opens a new request)* |
| `REJECTED` | — | Completed (outcome: rejected) | — | *(none — terminal; "Ask a follow-up")* |
| `FAILED` | — | — | **Failed** — "Execution failed and needs a decision (retry or stop here)." | Retry, Retry from architecture |
| `CANCELLED` | — | — | **Cancelled** — "This request was cancelled — no further work will happen." | *(none — terminal)* |

`allowed_actions` from `TaskStateMachine.allowed_actions()` also contains
internal, system-only transition names (`architecture_completed`,
`review_failed`, `advisory_completed`, …) that have no API route and were
previously rendered as dead buttons in `ActionBar`. `meaningfulActions()` in
`workStages.ts` filters the raw list down to the table above before it ever
reaches a button; the full raw list is still visible in the Advanced tab and
on the untouched `/tasks/{id}` page.

Progress steps (`ProgressSteps`, derived from the same fields, no fabricated
percentages): **Request understood → Investigation complete → Architecture
proposed → Approved → Implementing → Reviewed → Complete**, collapsed to
**Request understood → Investigation complete → Findings ready → Complete**
for requests that never required implementation (detected only once the
request reaches a finished status — see the code comment in `workStages.ts`
on why this can't be known earlier without a backend change).

## D. Component architecture

New:

- **`web/lib/workStages.ts`** — the single state-mapping module described
  above. `getWorkView(task)` is the only function anything else calls to
  decide what stage/badge/owner/progress/attention-reason to show.
- **`web/lib/textSummary.ts`** — text helpers for the Results view
  (`firstParagraph`, `filesChangedCount`, `reviewVerdictLabel`). No
  invented content: `reviewVerdictLabel` deliberately mirrors the backend's
  `parse_review_verdict` regex so the label can never disagree with what the
  workflow actually decided.
- **`web/components/Composer.tsx`** — the "ask the team" input. Creates a
  `Task` and fires `start_architecture` without blocking navigation. Used on
  the homepage and, for terminal Work Threads, as the "ask a follow-up" form
  (pre-selecting the same project).
- **`web/components/WorkRow.tsx`** — one row in a Work list (homepage
  sections, `/work`, filtered lists). Shared so stage badges render
  identically everywhere.
- **`web/components/DecisionCard.tsx`** — renders only `meaningfulActions()`,
  with inline note fields for actions that need one (reject/revise/send-back
  reasons). This is the entire "follow-up" surface — see Section E.
- **`web/components/ProgressSteps.tsx`**, **`RoleStatusPanel.tsx`** — small,
  presentational, driven entirely by `WorkView`/`Task`.
- **`web/app/work/page.tsx`** — the Work list, filterable, reads
  `?filter=` for continuity from homepage links.
- **`web/app/work/[id]/page.tsx`** — the Work Thread. Conversation column
  (request + role turns + notes + DecisionCard/composer) plus a side column
  (Progress, Team) plus tabs (Plan/Changes/Results/Activity/Advanced).

Changed:

- **`web/components/Sidebar.tsx`** — rebuilt as the persistent shell:
  **+ New request**, Home/Work/Projects/Team nav, a live "Recent" list (last
  6 updated Work items), Settings/Dashboard links.
- **`web/app/page.tsx`** — now the composer + attention/active/recent
  homepage (previously the KPI dashboard).
- **`web/app/dashboard/page.tsx`** (new route) — the former homepage
  content, verbatim, relocated.
- **`web/app/company/page.tsx`** — heading and intro copy changed to
  user-facing "Team" language; the org chart and manual-ask panel are
  unchanged (they are legitimately advanced/broader-than-one-request
  content, per the WP).
- **`web/app/projects/[id]/page.tsx`** — task links now point at
  `/work/{id}`; "no tasks yet" links to the composer with the project
  pre-selected (`/?project={id}`).

Untouched by design (progressive disclosure / advanced layer): `web/app/tasks/`,
`web/app/tasks/[id]/page.tsx`, `web/app/executions/`,
`web/components/ActionBar.tsx`, `web/components/EventLog.tsx`,
`web/components/DiffView.tsx`, `web/components/Markdown.tsx`,
`web/components/StatusBadge.tsx`. These remain reachable (task detail via
the Work Thread's Advanced tab and via direct links) and are exactly what
the WP-WEB-1 responsiveness contracts already test against — leaving them
alone was necessary to keep those contracts meaningful, not just convenient.

## E. Conversation → workflow-action mapping (Section 7 of the WP)

| Follow-up intent | Available when | Backend call |
|---|---|---|
| Approve plan | `AWAITING_ARCHITECTURE_APPROVAL` | `POST .../actions/approve-architecture` |
| Reject plan (with reason) | `AWAITING_ARCHITECTURE_APPROVAL` | `POST .../actions/reject-architecture` |
| Request changes (with notes) | `AWAITING_ARCHITECTURE_APPROVAL` | `POST .../actions/request-architecture-revision` |
| Accept result | `READY_FOR_HUMAN` | `POST .../actions/accept` |
| Reject result (with reason) | `READY_FOR_HUMAN` | `POST .../actions/reject` |
| Send back to Engineer (with notes) | `READY_FOR_HUMAN` | `POST .../actions/send-back` |
| Cancel | any non-terminal state | `POST .../actions/cancel` |
| Retry / Retry from architecture | `FAILED` | `POST .../actions/retry(-architecture)` |
| Resume implementation | `READY_TO_IMPLEMENT`, or `CHANGES_REQUESTED` with the repair budget exhausted | `POST .../actions/start-implementation` |
| Ask a follow-up | terminal (`ACCEPTED`/`REJECTED`/`CANCELLED`) | opens the Composer as a **new** Task in the same project — the backend has no notion of reopening a finished workflow, so this is honest about being a new request, not a continuation |

**Deliberately not implemented:** free-text messages while an execution is
actively running (`IMPLEMENTING`/`REVIEWING`/mid-repair `CHANGES_REQUESTED`
with `execution_status` in `QUEUED`/`STARTING`/`RUNNING`). There is no
backend channel to inject text into a running agent execution. The Work
Thread shows "*{role} is working — SceneWorks will ask you here when your
input is needed*" with only Cancel available, instead of a text box that
would create the illusion of influencing a run it cannot reach. This is the
single largest deliberately-deferred capability in this WP (see Section H).

The frontend also actively **hides** `start_implementation` while an
execution is already active for the same task (`CHANGES_REQUESTED` mid
auto-repair), even though `TaskStateMachine.allowed_actions()` technically
permits it — clicking it in that state would start a second concurrent graph
run against the same task. This was caught during implementation (see
Section G, "Journey B" fix) and is a frontend-only guard; no backend change
was made.

## F. API changes

**None.** Every view in this WP is composed from endpoints that already
existed before this work package:

- `GET /api/tasks`, `GET /api/tasks/{id}`, `POST /api/tasks`,
  `POST /api/tasks/{id}/actions/{action}` — request identity, status,
  current role, allowed actions, role results.
- `GET /api/tasks/{id}/diff` — git provenance (base commit, branch, result
  commit, commit list, stat).
- `GET /api/tasks/{id}/events` + `GET /api/events/stream` — bounded replay
  + incremental SSE, unchanged from WP-WEB-1.
- `GET /api/executions?task_id=` — used by the Work Thread's Advanced tab.
- `GET /api/projects` — the Composer's project picker.

A dedicated `GET /api/work/{id}` aggregation endpoint was considered
(Section 19 of the WP explicitly permits one) and rejected: the Work Thread
already fires its handful of requests in parallel (task, diff, events
replay, SSE connect — the same pattern `TaskDetailPage` used post-WP-WEB-1),
so there is no request waterfall to collapse, and a bespoke endpoint would
have meant a second, parallel serialization path for the same data
`TaskOut`/`DiffOut`/`EventOut` already provide. The homepage's three lists
(attention/active/recent) are all derived client-side from one
`GET /api/tasks?limit=50` call, already cached/deduped by
`web/lib/api.ts`. **The one thing this surfaced that did need a fix was on
the frontend, not the API surface** — see Section G.

## G. Bugs found and fixed during implementation

Building against a real (fake-agent) backend and real accumulated data
surfaced three genuine bugs, none of which were pre-existing — all were
introduced by this WP's own new frontend code and are fixed in this change:

1. **`isAdvisory()` misclassified any in-progress task as advisory-only.**
   The original heuristic was "no `implementation_summary` and no
   `review_result` yet," which is true for *every* task before it finishes
   implementing, not just ones that skip implementation. Fixed to only
   infer "advisory-only" once a task has reached a finished status
   (`READY_FOR_HUMAN`/`ACCEPTED`/`REJECTED`) without those fields ever being
   set. Caught by Journey B's browser test (`RoleStatusPanel` was hiding the
   Engineer/Reviewer rows for a task that was actively `IMPLEMENTING`).
2. **`reviewVerdictLabel()` could disagree with the backend's actual
   verdict.** The original heuristic ("does the text contain the word
   APPROVED") does not match `parse_review_verdict`'s actual rule
   (`VERDICT: ...` pattern, else "CHANGES_REQUESTED" substring, else default
   to APPROVED for non-empty text). Against the fake backend's literal
   `"fake backend completed"` review text, the old heuristic showed
   "Changes requested" for a task the backend had genuinely approved and
   that the founder had already accepted. Fixed to mirror the backend regex
   exactly. Caught by inspecting a real completed-result screenshot, not by
   a test — added to the regression list below.
3. **The homepage "Needs your attention" list had no cap.** Against a
   database with normal accumulated history it is unbounded and defeats the
   point of a scannable "what needs me" list. Fixed to show the 8 most
   recent, with a "View all (N) →" link into `/work?filter=attention`
   (which now reads `?filter=` for exactly this kind of link).

## H. Deferred items (explicitly out of scope for this WP)

- **Free-text follow-up while an execution is running.** No backend channel
  exists to inject a message into a live agent run; adding one is an
  orchestration change, out of scope here (see Section E).
- **Knowing "advisory-only" before completion.** `TaskOut` doesn't expose
  triage's `requires_implementation` decision. The Team/Engineer/Reviewer
  panel and progress stepper currently assume "will require implementation"
  until proven otherwise at completion — cosmetically over-eager during
  Investigation/Architecture for the minority of requests that turn out to
  be advisory-only, but self-corrects the moment the task finishes. Fixing
  this properly needs a small backend field, deliberately not added here per
  the WP's preference for minimal backend churn balanced against a
  cosmetic-only gap.
- **Visual design system.** Per Section 18 of the WP, this iteration reused
  the existing panel/badge/button vocabulary from `globals.css` rather than
  introducing a new visual language; that is WP-WEB-3.
- **Cursor pagination for the Work list / homepage sections.** Still
  bounded by simple `limit=` query params (200 for `/work`, 50 for the
  homepage), same as the rest of the app post-WP-WEB-1. Revisit if real
  datasets exceed those windows (already noted as a WP-WEB-1 follow-up).
- **A dedicated `/api/work/{id}` endpoint.** Considered and rejected for now
  (Section F); revisit if a future page needs data these composed calls
  can't reach efficiently.

## I. Screenshots

All captured against a real FastAPI backend running `FakeAgentBackend`
(`SCENEWORKS_DEFAULT_BACKEND=fake`) and a production Next.js build; two
mid-flight states (`Implementing`, `Reviewing`) and the `Failed` state use
mocked API responses because the fake backend completes in well under a
second, too fast to reliably capture live — the same technique used in
`web/e2e/work-journeys.spec.ts`.

| File | Shows |
|---|---|
| `01-homepage.png` | Homepage: composer, capped Needs-your-attention/Active-work/Recent-results |
| `02-new-request.png` | Composer with a request typed in, about to send |
| `03-architecture-approval.png` | Work Thread at `AWAITING_ARCHITECTURE_APPROVAL`: Plan tab, DecisionCard, Progress, Team |
| `04-completed-result.png` | Completed Work Thread: conversation, Results tab (verdict, commit, notes) |
| `05-active-implementing.png` | Work Thread at `IMPLEMENTING`, owner = Engineer |
| `06-engineering-review.png` | Work Thread at `REVIEWING`, owner = Reviewer |
| `07-failure-state.png` | Failed state: banner, Retry / Retry from architecture, no stale "running" indicator |
| `08-work-list.png` | `/work?filter=attention` — continuity from the homepage's "View all" link |

## J. Tests added and what they protect

`web/e2e/work-journeys.spec.ts` (new) — the five journeys from Section 20 of
the WP, browser-driven against mocked API responses (same technique as
`responsiveness.spec.ts`):

- **Journey A** (start work) — submitting the composer creates a Task,
  fires `start_architecture`, and navigates straight into its Work Thread
  without an intermediate "now go start it" step.
- **Journey B** (follow active work) — a simulated SSE `task.transitioned`
  event updates the stage badge and owner (`Implementing` → `Reviewing`)
  with no page reload and no polling wait.
- **Journey C** (needs user action) — an `AWAITING_ARCHITECTURE_APPROVAL`
  task surfaces on the homepage's Needs-your-attention list with its reason
  text, the architecture plan is visible without navigating to an
  execution/event page, and approving gives an immediate UI acknowledgement.
- **Journey D** (completion) — a `READY_FOR_HUMAN` task's Results tab shows
  a real reviewer verdict, commit, and files-changed count traceable to the
  mocked diff response, not an invented summary.
- **Journey E** (failure) — a `FAILED` task shows a clear failure banner and
  Retry action, never an indefinite "running" indicator.

Existing suites updated in place:

- `web/e2e/full-workflow.spec.ts` — smoke tests retargeted to the new
  routes/terminology (`/dashboard` instead of `/`, "Team" instead of
  "Company"), plus a new smoke test asserting the homepage composer is
  visible immediately.
- `web/e2e/responsiveness.spec.ts` — the dashboard-shell-while-pending test
  retargeted to `/dashboard` (its former home), and a new equivalent test
  added for the homepage: the composer must be interactive within 1s even
  while `/api/tasks` is held open, protecting the "discoverability without
  waiting" invariant for the new primary entry point.
- All four pre-existing responsiveness contracts (immediate action feedback,
  bounded event history, failed-acknowledgement recovery) still run against
  the untouched `/tasks/{id}` page and pass unmodified.

23/23 Playwright tests pass, reliably, across three full runs.

The backend pytest suite is unmodified by this WP — no `backend/app/*` file
was changed — but is worth reporting honestly rather than glossing over.
Three full runs on this machine during this session produced three
different failure sets (1 failure; then 3 failures + 1 error; then 7
failures + 1 error). Every failure was a timeout-shaped assertion ("task
never reached status X", a subprocess timeout, or a Windows process-count
check) — never a deterministic value mismatch — and no two runs failed on
the same set of tests. Re-running each run's failing tests in isolation
(nothing else launched deliberately in that window) passed every one,
quickly (e.g. one run's 3 failures passed in 32s standalone). The exact
root cause (machine load, or state left over between consecutive full runs
against the same `data/` checkpoint DB and worktree directories) was not
conclusively isolated — an attempt to capture a process-count baseline
during the third run itself misfired (a `find` invocation resolved to Git
Bash's POSIX `find` instead of the intended Windows `find.exe` and walked
the whole `C:` volume), so that specific diagnostic is not reliable
evidence either way. What is established: this WP touched zero backend
code; the failure pattern (different tests each run, timeout-only, clean
in isolation) is consistent with flakiness rather than a regression; and it
is recorded here rather than omitted because a work package claiming
"tests pass" should say what was actually observed.

## K. Usability invariants — verification

- **Discoverability** — the homepage's only above-the-fold content besides
  the nav is the composer; `full-workflow.spec.ts`'s homepage smoke test
  asserts the placeholder is visible on load.
- **Continuity** — submitting a request lands in its Work Thread
  (`/work/{id}`), not a resource list; Sidebar "Recent" and the homepage's
  "View all" links use `/work` and `/work?filter=`, never `/tasks` or
  `/executions`.
- **Explainability** — every Work Thread shows current stage (badge),
  current owner (Team panel), and next step (Progress panel + DecisionCard)
  from the same `WorkView` computed once per render.
- **Attention** — `needsAttention` in `workStages.ts` is the single source
  for what appears under "Needs your attention"; a task cannot be
  in that state without being reachable from the homepage.
- **Completion** — the Results tab only renders reviewer verdict/commit/
  files-changed when a matching field exists on the task or diff; nothing
  is synthesized (see the `reviewVerdictLabel` fix in Section G, which was
  specifically about not showing an incorrect verdict).
- **Evidence** — the Activity tab is the same `EventLog` component
  WP-WEB-1 already bounded/memoized, reused verbatim; conversation "Notes"
  are rendered from real `task.note` events, not paraphrased.
- **Advanced access** — Plan/Changes/Results/Activity/Advanced tabs plus the
  untouched `/tasks/{id}` and `/executions/{id}` pages keep every existing
  diagnostic reachable without it being the default view.
