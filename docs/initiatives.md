# Initiatives and work packages (WP5)

SceneWorks now separates **planning hierarchy** from **agent execution state**.

```text
Project
  └─ Initiative          durable objective / outcome
       └─ Work Package   bounded unit, order, dependencies, acceptance criteria
            └─ Task      existing executable SceneWorks workflow
```

Tasks remain the unit that moves through triage, architecture approval, Engineer, Reviewer and human acceptance. WP5 does not add another agent state machine around them.

## Initiative

An Initiative records a project-level objective that is too large to be treated as one coding task. It has a title, objective, description and lifecycle status:

`planned | active | blocked | completed | cancelled`

An Initiative cannot be marked `completed` while any of its work packages remains non-terminal (`planned`, `ready`, `active` or `blocked`).

## Work Package

A Work Package records:

- a stable key such as `WP1`;
- title and description;
- sequence/order;
- dependencies on other packages in the same Initiative;
- package-level acceptance criteria;
- lifecycle status;
- the Tasks assigned to it.

Keys are unique inside an Initiative. Dependency references must point to existing packages in the same Initiative, and updates are cycle-checked so SceneWorks cannot persist an impossible dependency graph.

Work-package statuses are:

`planned | ready | active | blocked | completed | cancelled`

## Task attachment

`POST /api/tasks` accepts an optional `work_package_id`.

SceneWorks validates that the package's Initiative belongs to the same Project as the Task. Existing Tasks without a work package remain valid, so the migration is backward-compatible.

A Work Package's acceptance criteria are deliberately **not copied automatically** into every child Task's engineering contract. A package can require several Tasks whose combined output satisfies the package-level criteria; requiring each individual Task to satisfy the whole package would produce false Reviewer failures. Each Task keeps its own binding WP4 engineering contract.

## API

- `GET /api/projects/{project_id}/initiatives`
- `POST /api/projects/{project_id}/initiatives`
- `GET /api/initiatives/{initiative_id}`
- `PATCH /api/initiatives/{initiative_id}`
- `GET /api/initiatives/{initiative_id}/work-packages`
- `POST /api/initiatives/{initiative_id}/work-packages`
- `GET /api/work-packages/{work_package_id}`
- `PATCH /api/work-packages/{work_package_id}`
- `POST /api/tasks` with optional `work_package_id`

The project UI links to an **Initiatives** planning page for lightweight objective and work-package creation. Full dependency, ordering, acceptance-criteria and status control remains available through the API.

## Current boundary

WP5 establishes durable decomposition and dependency state. It does **not yet autonomously decompose a natural-language objective into work packages or schedule ready packages**. That should be built as a governed planning workflow on top of this model, not as another prompt that writes directly into task execution state.

The existing LangGraph per-task typed state is still historically named `InitiativeState`; that internal name predates the persisted Initiative entity. It should be renamed to `WorkflowState` during WP7's WorkflowManager decomposition, when qualification protects the refactor.
