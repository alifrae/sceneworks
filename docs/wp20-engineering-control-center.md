# WP20 — Engineering Control Center and UI Rationalization

## Goal

WP20 makes the web UI reflect the engineering-control architecture delivered by
WP14–WP18. It does not add another orchestration or ticketing system.

The main operator surfaces are now:

```text
Home      create work and see what needs attention
Work      full SceneWorks task lifecycle
Issues    lightweight bugs/features/ideas over the existing Task model
Control   EngineeringSession + managed PCS + evidence snapshot
Projects  repository registrations and lifecycle
Settings  agent/model/MCP/runtime configuration
```

## Engineering Control Center

`GET /api/control-center` is a bounded, read-only aggregate for the web UI. It
returns:

- project count;
- active/attention task counts;
- open/closed engineering-issue counts;
- recent EngineeringSessions;
- recent managed PCS runs;
- recent EngineeringEvidence metadata.

It deliberately does **not** return:

- EngineeringEvidence payloads;
- worktree host paths;
- arbitrary process details;
- mutation endpoints.

Engineering mutation stays in the existing governed task/MCP/runtime surfaces.
The web control center is an operational view, not a second execution authority.

## Lightweight issues

SceneWorks already had `work_item_type = task | bug | feature | idea`, priority,
execution intent and the Task lifecycle. WP20 exposes those capabilities as an
Issues view instead of introducing a separate Issue table.

The Issues page supports:

- quick bug/feature/idea capture;
- project selection;
- low/medium/high priority;
- open/closed/all filtering;
- inline issue type and priority edits;
- direct navigation to the normal SceneWorks work item.

Non-goals are intentional: no sprints, story points, epics, assignees, kanban
workflow or duplicated status machine. A bug that needs implementation becomes
normal SceneWorks work; its evidence and engineering history remain attached to
the same Task id.

## Project lifecycle

Project cards now keep repository identity, registration metadata and actions in
one place. Individual **Unregister from SceneWorks** uses:

```text
purge_history=true
force=true
```

This removes stale SceneWorks-owned task/provider-session/configuration records
without touching the Git repository or external PCS assets.

`force=true` does not bypass the WP16 safety invariant: a project with an active
EngineeringSession or managed PCS process still returns 409 until the process is
stopped and the session is closed. SceneWorks must never delete the authority
record for a live process/worktree.

## Settings

Settings is restored as a primary navigation item. The existing settings page
remains authoritative for:

- default agent backend;
- provider executables/models;
- provider-neutral model routing;
- worktree root and execution timeout;
- MCP enable/mode/tool bounds;
- Advanced EngineeringSession permission ceiling, including PCS GUI observe and
  GUI automate permissions.

## Home and visual system

The previous marketing-style hero and three generic capability claims were
removed. Home is now an operational start page:

- SceneWorks name and one-line functional description;
- work composer;
- needs-attention list;
- active work;
- recent results;
- direct links to Issues, Control and Projects.

WP20 adds `web/app/wp20.css`, loaded after the older visual layers. The palette
moves from blue/purple gradients to neutral graphite/stone surfaces with a warm
accent. Existing semantic success/warning/danger states remain distinct.

Keeping WP20 overrides in a separate stylesheet avoids a broad unrelated rewrite
of the older CSS while making the new operator-facing visual baseline explicit.

## Acceptance criteria

WP20 is complete when:

1. Settings is visible in primary navigation and the existing settings route
   remains usable.
2. Home contains no generic AI-marketing hero or three-feature trust strip.
3. Primary branding/panels/navigation no longer use the blue/purple theme.
4. A stale project with task/history records can be unregistered from the normal
   Projects UI without deleting its repository/assets.
5. Active EngineeringSessions/PCS runs still block project deletion.
6. Issues are tracked through existing Task semantics rather than a Jira-like
   parallel domain model.
7. `/api/control-center` exposes bounded operational metadata without evidence
   payloads or host worktree paths.
8. The Control page surfaces EngineeringSessions, PCS runs and recent evidence.
9. WP14–WP18 regressions and the full SceneWorks qualification remain green.
