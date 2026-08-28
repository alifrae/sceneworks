# WP13 — Work Management and Intent Routing

## Goal

Make SceneWorks useful as both a lightweight engineering backlog and an execution control plane without turning it into Jira or adding a second orchestration framework.

WP13 keeps the existing `Project → Initiative → WorkPackage → Task` hierarchy. A Task remains the executable unit. A task with status `NEW` is also the lightweight backlog item; no parallel issue database is introduced.

## Work item classification

Task now carries two independent concepts:

- `work_item_type`: `task`, `bug`, `feature`, or `idea`. This is organizational metadata only.
- `requested_mode`: `auto`, `change`, `investigate`, `plan`, or `ask`. This expresses execution intent.
- `resolved_mode`: the effective non-auto mode after routing. It is stored separately so model inference never overwrites the user's original request.

Existing databases migrate to `work_item_type=task`, `requested_mode=auto`, `resolved_mode=NULL`.

## Backlog semantics

The composer provides two actions:

- **Save to backlog** creates the task, binds any attachments, and leaves it `NEW`. No agent is invoked.
- **Start now** creates the same task and starts the existing governed workflow.

While a task remains `NEW`, its type, requested mode and priority can be edited. Once execution starts, those intent fields are frozen just like the engineering contract and attachment set.

The Work page provides operational buckets rather than project-management ceremony:

- Backlog — `NEW`
- Active — execution/work in progress
- Needs attention — human decision or failure/exception requiring attention
- Done — accepted, rejected, or cancelled terminal work
- All

It also supports text, project, type, mode and priority filters. Counts are shown directly; charts, sprints, story points, velocity, assignees and Kanban machinery are deliberately outside WP13.

## Intent authority and routing

The existing LangGraph workflow remains canonical. WP13 does not add graph topology or task states.

Triage still classifies the request and selects useful advisory roles. The requested mode then constrains that result deterministically:

| Mode | Routing contract |
| --- | --- |
| `auto` | Resolve from triage. Implementation → `change`; architecture/technology decision → `plan`; product question → `ask`; other non-implementation → `investigate`. |
| `change` | `requires_implementation=true`. Triage cannot downgrade it to advisory-only work. Existing architecture/approval and bounded-bug skip policies still apply. |
| `investigate` | Read-only Architect analysis; no Engineer execution. |
| `plan` | Read-only architecture/design output; no Engineer execution. |
| `ask` | Direct read-only answer through the Architect execution path; advisory fan-out is disabled. |

For read-only modes, the Architect prompt receives explicit intent guidance so it returns the requested artifact instead of treating every request as a code-change plan.

Every routing decision emits `workflow.routing.policy` with:

- `requested_mode`
- `resolved_mode`
- `mode_source` (`user` or `inferred`)
- routing decision and reason

This makes intent selection inspectable without promoting model output to hidden authority.

## API

`POST /api/tasks` accepts:

- `work_item_type`
- `requested_mode`

`PATCH /api/tasks/{task_id}` updates lightweight backlog metadata while the task is `NEW`.

`GET /api/tasks` additionally supports `work_item_type`, `mode`, `priority`, and `query` filters.

The existing `start-architecture` action is retained on the wire for backward compatibility. The UI labels it **Start work** because the actual path is now intent-dependent.

## MCP

The WP13 MCP extension is layered over the attachment-aware server rather than modifying the protocol core. Task reads expose:

- `work_item_type`
- `requested_mode`
- `resolved_mode`
- `effective_mode`

`sceneworks.create_task` accepts the new fields and still creates backlog work without starting it.

## Non-goals

WP13 intentionally does not add:

- a separate issue tracker
- Jira synchronization
- sprints, estimates or story points
- Kanban drag-and-drop
- charts on the Work page
- another agent/workflow framework
- automatic merging or reduced human authority

## Verification

Targeted tests cover:

- compatibility defaults (`task` + `auto`)
- editing metadata only while `NEW`
- type/mode/priority/text filtering
- deterministic Auto resolution
- explicit `investigate` resisting a triage attempt to promote it to implementation
- explicit `change` resisting a triage attempt to downgrade it to non-implementation
- persisted routing provenance

Release version remains `3.0.0` during the WP12/WP13 qualification period. A version bump should be made at the planned release boundary rather than per work package.
