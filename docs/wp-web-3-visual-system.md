# WP-WEB-3 — Visual System and UI Polish

Status: implemented and verified on 2026-08-13.

This report documents the visual audit, the design system, the dependency
decisions, and the verification evidence for WP-WEB-3. It builds on
WP-WEB-1 (`docs/wp-web-1-performance.md`, responsiveness contracts) and
WP-WEB-2 (`docs/wp-web-2-conversation-model.md`, the conversation-first
information architecture and Work Thread model) and keeps both green
throughout. No backend file was touched, no workflow semantics changed, no
new Work backend entity was introduced, and no new npm dependency was
added.

## A. Visual audit (before this pass)

Read from every page/component in `web/app` and `web/components`, and the
739-line hand-rolled `web/app/globals.css`, before any change was made.

- **No hierarchy on the homepage.** "Needs your attention", "Active work",
  and "Recent results" were three identical bordered `.panel` cards.
  Nothing told the eye which one mattered, contrary to WP-WEB-2's own
  interaction model.
- **One body font size for everything.** 14px flat, no defined type scale;
  `<h3>` was repurposed as an uppercase eyebrow rather than a real heading
  level, so there was no distinction between "page title", "section title",
  and "work/thread title".
- **Spacing was ad hoc.** Dozens of inline `style={{ marginTop: 16 }}` /
  `style={{ display: "grid", gridTemplateColumns: ... }}` scattered across
  pages instead of a scale — most visibly duplicated between
  `app/tasks/[id]/page.tsx` and `app/executions/[id]/page.tsx`.
- **Three status-color systems that disagreed.** The authoritative
  `.stage-badge` vocabulary (`lib/workStages.ts`), a separate raw-status
  color map (`STATUS_COLORS` in `lib/format.ts`) driving `StatusBadge.tsx`,
  and a *third*, independently duplicated color map local to
  `app/executions/page.tsx`.
- **`Markdown.tsx` had no fenced-code-block support.** Architecture plans
  or reviewer notes containing a ` ``` ` block rendered it as a flat,
  unformatted paragraph.
- **The diff view was one unparsed blob.** `DiffView.tsx` rendered
  `diff.full` as a single scrolling `<pre>`, even though the backend's
  unified diff text already contains per-file `diff --git a/... b/...`
  boundaries that were simply never parsed.
- **No "blocked" visual state.** `ProgressSteps.tsx` had no way to show a
  blocked or failed task's stuck step differently from a step that simply
  hadn't started yet — both rendered as plain gray "○ pending".
- **Destructive actions were alarming, not subtle.** `.btn.danger` was a
  solid red fill applied to every destructive action, including plain
  `cancel` — WP-WEB-3 explicitly asks for "destructive/subtle", not equal
  visual weight to primary.
- **Dashboard and Settings were buried.** They lived in a sidebar prose
  footnote ("...decides what to integrate. *Settings · Dashboard*"),
  not real navigation.
- **No `:focus-visible` styling anywhere** in `globals.css` — keyboard
  focus relied entirely on the browser default.
- **Two Dashboard status badges were actually invisible.** `.badge.success`
  / `.badge.error` were referenced in `app/dashboard/page.tsx` and
  `app/settings/page.tsx` but never defined in CSS — white text (the
  `.badge` base color) on no background at all.

## B. Design system

Defined once, as tokens in `web/app/globals.css`, and reused everywhere —
including the legacy/advanced pages (`/tasks`, `/executions`), which
inherit it automatically through shared class names rather than being
individually rewritten.

### Typography

| Level | Spec |
|---|---|
| Page title (`h1` default) | 20px / 650 |
| Work/thread title (`.thread-title`) | 17px / 600, `overflow-wrap: anywhere` |
| Section title (`h2`, `h3` eyebrow) | 14px / 600 |
| Body | 13.5px / 1.55 |
| Secondary text (`.muted`) | 12.5px |
| Metadata (`.meta`, timestamps) | 12px, dim |
| Code / technical value (`.mono`, `pre`, `code`) | 12.5px mono |

System font stack throughout (`-apple-system, "Segoe UI", Roboto, ...` /
`SFMono-Regular, Consolas, Menlo, ...`) — no webfont network cost.

### Spacing

4px base unit: `--space-1` … `--space-7` = 4/8/12/16/24/32/48px. Page
content padding 24px, section gaps 24px, card padding 16px, row spacing
8–12px, sidebar item padding 6/10px.

### Surfaces

| Token | Use |
|---|---|
| `--surface-page` | page background |
| `--surface-primary` | bordered white panel (composer, thread conversation, forms, tabs container) |
| `--surface-subtle` | borderless — homepage work-queue sections, nested notes |
| `--surface-attention` | amber-tinted — reserved for what actually needs the user |

### Borders / radius / shadows

`--radius-sm: 6px` (buttons/inputs/badges), `--radius-md: 8px` (panels). No
shadows introduced — flat, matching the "developer tool, not decorated
SaaS dashboard" direction in the work package.

### Status semantics

`.stage-badge` (the `lib/workStages.ts` vocabulary) is the one
user-facing authority — unchanged in meaning, only its tokens were
formalized. Six semantic pairs, reused by stage badges, progress-step
markers, and role-status dots so they never visually disagree:

| Semantic | bg / fg |
|---|---|
| neutral (submitted, cancelled) | `#eef2f7` / `#475569` |
| info (investigating, architecture) | `#dbeafe` / `#1d4ed8` |
| progress (implementing, reviewing) | `#ede9fe` / `#6d28d9` |
| success (completed) | `#dcfce7` / `#15803d` |
| warning (needs input, blocked) | `#fef3c7` / `#92400e` |
| danger (failed) | `#fee2e2` / `#b91c1c` |

Raw/technical status values (the 14-value `TaskStatus`/`Execution` enum,
shown only on `/tasks`, `/executions`, and the Work Thread's Advanced tab)
were deliberately moved *off* this colorful vocabulary onto a new
`.status-chip` — a small, neutral, monospace chip — so there is exactly
one colorful interpretation of "status" in the app, not two competing
ones.

## C. Dependency decisions

| Dependency | Decision | Reason |
|---|---|---|
| Hand-rolled CSS (`globals.css`) | **KEEP** | Expanded in place — a token system and refined primitives, not a replacement. Zero UI dependencies existed before this WP. |
| Tailwind CSS | **NOT ADDED** | Would introduce a new build pipeline (postcss config, content scanning) purely to re-skin elements that are already simple — buttons, inputs, badges, tabs, panels. |
| Radix UI / shadcn | **NOT ADDED** | The app has no dialogs, menus, comboboxes, or popovers. Radix's value (focus trapping, roving tabindex, portal management) isn't needed anywhere in this app today — the one tab bar in the app got hand-written `role="tablist"`/`role="tab"` + arrow-key navigation instead, which is a few lines of code against a single component. |
| `next` / `react` / `playwright` | **KEEP** | Unchanged. |

No new package was added to `web/package.json`.

## D. Changed component map

| File | What changed / why |
|---|---|
| `app/globals.css` | Foundation rewrite: type/spacing/surface/status tokens, button hierarchy (primary solid, neutral outline, danger restyled subtle), focus-visible ring, tab/badge/panel primitives. |
| `components/Sidebar.tsx` | Dashboard/Settings promoted from a prose footnote into a real, visually secondary nav group; brand block trimmed to one line. |
| `app/page.tsx` | Homepage: composer de-emphasizes the page title instead of competing with it; "Active work"/"Recent results" moved from bordered `.panel` cards to borderless work-queue sections; "Needs your attention" gets a distinct tinted-surface treatment only when it actually has something in it. |
| `app/work/page.tsx`, `components/WorkRow.tsx` | Filter row restyled as a segmented control; row title truncates with a `title` tooltip instead of wrapping and pushing the stage badge/timestamp around. |
| `app/work/[id]/page.tsx` | Thread title hierarchy (`.thread-title`); tab bar gets real `role="tablist"`/`role="tab"`/`aria-selected` + left/right arrow-key navigation; Results tab reordered outcome-first (verdict badge → summary → commit/files → full reviewer notes); Advanced tab regrouped into Task/Execution/Git-worktree/Links sections. |
| `components/ProgressSteps.tsx` | New "blocked"/"failed" visual state — the first not-yet-done step now renders amber (blocked) or red (failed) instead of being indistinguishable from a step that simply hasn't started. Presentation-only; consumes the `exceptional` value `WorkThreadPage` already computes. |
| `components/RoleStatusPanel.tsx` | Active rows now show an explicit "working" label alongside the existing dot/color, so state is never color-only. |
| `components/DecisionCard.tsx` | Actions grouped under `.decision-actions`; destructive actions (`reject`, `reject_architecture`, `cancel`) now render at `.btn.danger.small` — subtle red text/border, not a solid alarming fill. |
| `components/Markdown.tsx` | Added fenced-code-block (` ``` `) support, previously missing entirely; heading size now varies by `#`/`##`/`###`/`####` level instead of one flat style. |
| `components/DiffView.tsx` | Client-side parse of `diff.full` on its existing `diff --git a/... b/...` boundaries into a per-file list with `+N`/`-M` pills and per-file expand/collapse. No backend change — the file boundaries were already present in the text. |
| `components/StatusBadge.tsx`, `lib/format.ts` | Raw status restyled to `.status-chip` (neutral mono); the now-unused `STATUS_COLORS` map deleted. |
| `app/executions/page.tsx` | Reuses the shared `StatusBadge` instead of its own separately-duplicated color map. |
| `app/settings/page.tsx` | Backend-availability badge switched from an ad hoc inline `style={{background:"#22c55e"}}` to the (now-defined) `.badge.success`/`.badge.error` classes. |
| `app/executions/[id]/page.tsx`, `app/tasks/[id]/page.tsx` | Duplicated `display:grid; gridTemplateColumns:"...1fr 1fr"` inline styles replaced with the shared `.split-2` utility class. |

Untouched by design (progressive-disclosure / advanced layer, per WP-WEB-2):
`components/ActionBar.tsx`, `components/EventLog.tsx`'s internal structure,
`app/tasks/page.tsx`, `app/projects/*`, `app/company/page.tsx` — these
inherit the new primitives automatically through shared class names
(`.btn`, `.panel`, `.badge`, `table.grid`) without any JSX changes.

## E. Bugs found and fixed during this WP

1. **Two Dashboard/Settings status badges were invisible** (pre-existing,
   found during the audit — see Section A). `.badge.success`/`.badge.error`
   were referenced in JSX but never defined in CSS. Fixed by defining them
   as part of the badge token cleanup.
2. **A duplicated raw-status color map** existed independently in
   `app/executions/page.tsx` in addition to the one behind `StatusBadge`
   (pre-existing). Consolidated to one component per Section D.
3. **Adding real ARIA tab semantics changed the tab buttons' accessible
   role** from the implicit `button` to `tab` (setting `role="tab"`
   overrides the implicit role). This broke one existing Playwright
   locator, `getByRole("button", { name: "Results" })`, in
   `web/e2e/work-journeys.spec.ts`. Fixed by updating that one locator to
   `getByRole("tab", ...)` — the more precise, and now correct, query — not
   by reverting the accessibility improvement.
4. **Showing the reviewer's full verdict text in the Results tab** (a
   deliberate enhancement, Section D) made `.result-summary`'s "Approved"
   text assertion ambiguous: it now matches both the outcome badge and the
   substring "APPROVED" inside "VERDICT: APPROVED" in the full reviewer
   text below it. Fixed by scoping that one assertion to `.result-outcome`,
   the badge that is the actual authoritative verdict indicator.

## F. Deferred items

- **RoleStatusPanel's "not participating" state.** The work package names
  four Team states (working / waiting / completed / not participating);
  only three are backed by data `TaskOut` exposes per task
  (architect/engineer/reviewer only — advisory roles like Product/CTO/
  Technical Expert have no per-task participation field). Adding a fourth
  row for those roles would mean inventing information the backend doesn't
  provide. Deferred rather than fabricated; would need a small backend
  field, which is out of scope (no backend changes in this WP).
- **Dark mode.** Not requested; the app is light-only throughout, unchanged.
- **Mobile-first layout.** Explicitly out of scope — WP-WEB-3 states
  SceneWorks is desktop-first and this WP is not "building a phone-first
  mobile application."
- **Syntax highlighting in Markdown code blocks.** Fenced code blocks
  render as plain monospace (Section D) rather than pulling in a
  highlighter dependency, consistent with the "no new dependency without a
  demonstrated need" decision in Section C.
- **Tailwind / Radix / shadcn adoption.** Deliberately not pursued — see
  Section C.

## G. Verification

| Check | Result |
|---|---|
| `npm run build` (production build + TypeScript check) | ✓ compiled, ✓ no type errors |
| Existing Playwright suite (`full-workflow`, `responsiveness`, `work-journeys`) | 23/23 passed |
| New `web/e2e/visual-polish.spec.ts` | 10/10 passed |
| Backend `uv run pytest` (observational — no backend file changed) | 132/132 passed |

One responsiveness test (`action feedback appears before a slow backend
acknowledgement`, budget < 200ms) measured 274ms during a full-suite run
under concurrent load (backend + build + browser on the same machine).
Three isolated re-runs immediately after measured 122ms / 152ms / 164ms —
well under budget. Reported as a load artifact of this session, not a
regression: no code on that path (`app/tasks/[id]/page.tsx`, `ActionBar`)
was touched by this WP.

The backend pytest run produced 38 `PytestUnraisableExceptionWarning`
(`ResourceWarning: unclosed transport`) messages — asyncio subprocess
teardown noise on Windows, unrelated to this WP (no backend file changed)
and out of scope per the work package's own instruction not to solve
backend pytest flakiness here.

### Tests added

`web/e2e/visual-polish.spec.ts` (new, 10 tests), mocked-API style matching
`work-journeys.spec.ts`:

- Six representative Work Thread states (Investigating, Architecture
  approval, Implementing, Reviewing, Completed, Failed) each render with a
  visible stage badge and at least one primary action, and without
  horizontal page overflow.
- Architecture approval exposes "Approve plan" (primary), "Request
  changes" (neither primary nor danger), and "Reject plan" (danger)
  simultaneously, with distinct classes — protecting the decision-action
  hierarchy from Section 8 of the work package.
- Three long-content cases (a long work title, a long architecture plan
  with a fenced code block and an unbroken long line, a long nested diff
  file path) each render without `document.documentElement.scrollWidth`
  exceeding `clientWidth`.

Two edits to existing specs, both explained in Section E: one locator in
`work-journeys.spec.ts` updated from `getByRole("button", ...)` to
`getByRole("tab", ...)` for the Results tab click, and one assertion
re-scoped from `.result-summary` to `.result-outcome` to disambiguate the
"Approved" text match.

## Screenshots

Captured with mocked API fixtures (same technique as
`work-journeys.spec.ts`) at 1440×900, before and after this WP's changes,
including one deliberately long work title, a long architecture plan with
a fenced code block, and a long nested diff file path.

| File | Shows |
|---|---|
| `01-homepage.png` | Homepage |
| `02-work-list.png` | Work list |
| `03-thread-architect.png` | Work Thread — Architect (Investigating) |
| `04-architecture-approval.png` | Work Thread — Architecture approval |
| `05-implementing.png` | Work Thread — Implementing |
| `06-reviewing.png` | Work Thread — Reviewing |
| `07-completed.png` | Work Thread — Completed result |
| `08-failed.png` | Work Thread — Failed / blocked |
| `09-plan-tab.png` | Plan tab (long architecture text + fenced code) |
| `10-changes-tab.png` | Changes tab (per-file diff) |
| `11-activity-tab.png` | Activity tab |
| `12-advanced-tab.png` | Advanced tab |
| `13-dashboard.png` | Dashboard |

Full before/after images are not committed to the repository (binary,
regeneratable from `web/e2e/` fixtures); they were delivered alongside
this report as a side-by-side gallery.
