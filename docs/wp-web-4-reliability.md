# WP-WEB-4 — Web UI Reliability and Slowness

Status: implemented and verified on 2026-08-13.

This report records the root-cause trace and the changes made. Measurements
are local reference numbers (Windows dev box, existing dataset, fake
backend), not synthetic SLOs.

## Symptoms reproduced

- Home: two identical `Cannot reach the SceneWorks API: TypeError: Failed to
  fetch` banners; "Active work" and "Recent results" pinned on `Loading…`
  while only "Needs your attention" showed the error.
- Team: org chart rendered as bare connector pipes (`│││`, zero `.org-node`)
  after the roles fetch failed, plus a misleading "No decisions stored yet."
  while the artifacts fetch had actually failed.
- Page load / navigation slower than it should be, intermittent failures
  across pages.

## Root-cause report

### Why requests fail

| Cause | Evidence | Fix | Result |
|---|---|---|---|
| Port mismatch: `docs/development.md` instructed `uv run uvicorn app.main:app --reload`. Uvicorn's CLI default port is **8000**; the frontend and `SCENEWORKS_PORT` target **8010**. | The backend running on this machine was actually listening on 8000 while every page fetched 8010 → ECONNREFUSED → `TypeError: Failed to fetch` on every request. "Intermittent" because the E2E instructions did pass `--port 8010`. | `python -m app.main` runner binds `settings.host`/`settings.port` (8010) with reload; docs updated; CLI form documented with explicit `--port 8010`. | Server and client agree by construction. Verified: runner on 8011 override and 8010 default both serve `/api/health`. |
| CORS allow-list contained only `http://localhost:3000`. Any other local origin (`next dev` bumped to 3001 when the port is busy, `http://127.0.0.1:3000`, …) made every response CORS-blocked → the same bare `TypeError: Failed to fetch`. | Browser behavior of CORS-blocked fetch; the single-origin default in `settings.py`. | `allow_origin_regex` for `localhost`/`127.0.0.1` on any port (API binds localhost only — the same trust model as before). | Local origins on any port pass; foreign origins stay blocked (pinned by `backend/tests/test_cors.py`). |
| Backend startup takes seconds and `--reload` repeats it. Measured **>8 s** cold start on this box (imports, DB init, interrupted-execution recovery, workflow recovery that may auto-resume agent runs). Requests in that window fail. | Timed from process spawn to first `/api/health` response. | Startup work stays (it is required), but the frontend no longer treats one-shot fetch failures as permanent (see below). | Transient outages self-heal without a reload. |
| No client retry for one-shot fetches. Team fetched roles **once**, Composer fetched projects once; a single failure left permanent error states until a manual reload. Home never cleared its error even after later polls succeeded. | Team org chart stayed broken indefinitely after one failed request; Home banner persisted while data rendered. | Composer retries every 15 s (and on tab focus); Team re-polls roles/projects and offers Retry; the shared tasks poller clears the error on success. | Pages recover automatically once the API answers. |

### Duplicate, redundant and wasteful requests

| Observed | Evidence | Fix |
|---|---|---|
| Sidebar and Home each polled `/api/tasks` with different query strings (`limit=6` every 15 s and `limit=50` every 8 s), so the per-key request cache could never coalesce them. | Measured baseline: Home issued `/api/tasks?limit=50` ×2 and `/api/tasks?limit=6` ×1 in a 12 s window. | New `web/lib/useTasks.ts`: one app-wide snapshot, one poller (8 s), one request per tick, shared by Home, Sidebar and the unfiltered Work list. |
| Polls kept running in hidden tabs (Home, Sidebar, Team, Composer). | Code audit; no `visibilityState` checks. | All pollers are visibility-aware and also refresh on `visibilitychange`. |
| Navigation refetched slow-changing resources (projects, roles, settings, backends) for every consumer. | 2 s default cache TTL was shorter than any navigation. | Per-resource TTLs: projects 30 s, roles 60 s, settings/backends 30 s, artifacts 4 s; settings mutation also invalidates roles/company/backends. |
| EventLog reconnected SSE on a fixed 2 s loop while the API was down — an endless stream of failing connections. | Code audit. | Exponential backoff 2 s → 30 s cap, reset on open. |
| Team polled artifacts every 5 s even while hidden. | Measured: 3 artifact requests in 12 s regardless of tab state. | Visibility-aware (cadence kept — documented as WP-WEB-1 deferred work). |

### UI defects on failure

| Defect | Fix |
|---|---|
| Home showed duplicate banners (Composer + section) and 2/3 sections stuck on `Loading…`. | One page-level banner; every section renders a terminal state (`Unavailable — API offline.`); Composer accepts `suppressError` on Home and shows a non-banner note; banner clears on recovery. |
| Team rendered a broken org chart from an empty `roles` default. | `roles: Role[] \| null` with an explicit "Loading team…" state and an error state with Retry; chart only renders with data. Artifacts/projects failures show honest error states instead of "No decisions stored yet." / empty select. |
| Raw `TypeError: Failed to fetch` taught the user nothing. | Transport failures are wrapped as `ApiError(status 0)` with an actionable message naming the API URL; `errorMessage()` used by touched pages. |

## Before / after

Request counts (single page, one browser, mock-free, local API):

| Page | Window | Before | After |
|---|---|---|---|
| Home | 12 s | `/api/tasks?limit=50` ×2, `/api/tasks?limit=6` ×1, `/api/projects` ×1 (**4 requests, 2 task-list streams**) | `/api/tasks?limit=200` ×2 (shared), `/api/projects` ×1 (**3 requests, 1 stream**) |
| Home | 30 s | 7 task-list requests (50-limit + 6-limit streams) + 1 projects = **8** | 4 shared task-list requests + 1 projects = **5** |
| Team | 30 s | artifacts ×7, roles ×1, projects ×1, `limit=6` ×3 = **12** | artifacts ×6–7, roles ×1, projects ×1, shared ×4 = **12** (same total, but the task list is now the shared stream; hidden tabs pause everything) |
| Home → Work navigation | — | refetches tasks + projects on arrival | reuses the live snapshot and 30 s projects cache (no refetch) |

Timings (production build, warmed, local):

| Metric | Before (dev server, same machine) | After (prod build) |
|---|---|---|
| Home page load | 739–1243 ms (dev compile noise) | **207–283 ms** |
| Team page load | 1061 ms | **285–291 ms** |
| API server medians | unchanged by this work | `/api/tasks?limit=200` 26 ms, `/api/projects` 22 ms, `/api/company/roles` 1.9 ms, `/api/company/artifacts` 7.4 ms, `/api/dashboard` 26 ms |

Note: Team's total request count is flat because the shared 8 s tick replaces
the sidebar's old 15 s tick there; the win is that every page now uses one
task-list stream, hidden tabs stop polling, and navigation is cache-coherent.
The backend list endpoints were already optimized in WP-WEB-1 and were not a
bottleneck; startup latency is dominated by imports/recovery, which the
frontend now tolerates by self-healing.

## Changes

Backend:

- `backend/app/main.py` — `allow_origin_regex` for local origins; `__main__`
  runner binding the configured port (reload on).
- `backend/tests/test_cors.py` — 4 new tests pinning local any-port allowance,
  preflight, foreign-origin blocking, and explicit-origin compat.

Frontend:

- `web/lib/api.ts` — transport-error wrapping (`ApiError` status 0, actionable
  message), per-resource cache TTLs, settings-mutation invalidation scope,
  `errorMessage()` helper.
- `web/lib/useTasks.ts` — shared tasks snapshot with a single
  visibility-aware poller.
- `web/app/page.tsx` — single error banner, terminal states for all sections,
  shared snapshot, `suppressError` on Composer.
- `web/app/company/page.tsx` — loading/error states for roles, artifacts,
  projects; Retry; visibility-aware polling.
- `web/app/work/page.tsx` — unfiltered list uses the shared snapshot;
  filtered queries keep their own loop.
- `web/components/Sidebar.tsx` — consumes the shared snapshot (no own poll).
- `web/components/Composer.tsx` — `suppressError`, 15 s / focus retry.
- `web/components/EventLog.tsx` — SSE reconnect backoff.
- `docs/development.md` — corrected backend run commands.

## Regression coverage

New `web/e2e/reliability.spec.ts` (7 tests, mocked API):

- outage → exactly one error banner, zero `Loading…` states, composer note
  instead of a duplicate banner;
- Home recovers when the API returns (error clears, sections render);
- sidebar + page share a single task-list request per poll tick (no
  `limit=6` stream, ≤ one request per tick);
- shared poller pauses while the tab is hidden and refreshes on focus;
- Team renders an error state with Retry, never a broken org chart, and
  failures never masquerade as empty data;
- Team recovers via Retry and renders all 7 org nodes;
- roles loading state precedes the chart.

`backend/tests/test_cors.py` — 4 tests as above.

Adjusted existing specs: Journey C clicks the work-row link (the sidebar now
correctly shows the same task, per the shared snapshot).

## Verification

- `npx playwright test` (production build): **40/40 passed**.
- Backend `pytest -q`: **136 passed** (132 existing + 4 new).
- `npx tsc --noEmit`: clean; production `next build`: clean.
- Live check on this machine: backend was on port 8000 against a 8010
  client; after the fix, `python -m app.main` serves 8010 and the UI loads
  without errors.

## Deferred

- Team's 5 s artifact poll remains polling-based (WP-WEB-1 noted an
  event-driven artifact stream as follow-up).
- The shared tasks poller could pause at a longer interval when only the
  sidebar is subscribed; current 8 s tick matches the old Home cadence.
- Backend cold-start time (imports + recovery) is unchanged; if it grows,
  serve `/api/health` earlier or move recovery after first listen.
