# Engineering contracts (WP4)

SceneWorks tasks can carry a structured engineering contract: the checkable definition of what the task is allowed and required to do.

The contract is provider-independent and is injected into triage and every task role from the same persisted task record. It is not free-text advice appended by an individual agent.

## Contract fields

- `required_behavior` — behavior that must exist after the task.
- `allowed_scope` — files, subsystems, or areas the implementation may change.
- `forbidden_changes` — explicit non-goals and protected behavior/surfaces.
- `architecture_constraints` — design or dependency rules the implementation must preserve.
- `required_tests` — commands or evidence that must be produced.
- `performance_requirements` — measurable performance obligations.
- `compatibility_requirements` — supported versions, APIs, formats, or callers that must remain compatible.
- `acceptance_criteria` — observable conditions for considering the work complete.

Every field is optional. An empty contract preserves the pre-WP4 workflow.

## Lifecycle and authority

A contract is created with the task or replaced through `PUT /api/tasks/{task_id}/contract` while the task is `NEW`.

Once architecture starts, the contract is immutable. This is deliberate: changing requirements after the Architect has analyzed one contract would make the stored architecture, Engineer implementation, and Reviewer verdict refer to different definitions of done. A changed requirement should be handled explicitly rather than silently mutating an in-flight task.

The prompt builder labels a non-empty contract **binding** and tells every role not to silently relax, reinterpret, or expand it. If repository evidence conflicts with a clause, the role must surface the conflict.

The Reviewer receives an additional rule: evaluate every applicable clause, and request changes when a required criterion or test cannot be verified rather than assuming it passed.

## UI

The primary **Ask the team** composer exposes the four most execution-critical dimensions before the workflow starts:

- acceptance criteria;
- allowed scope;
- forbidden changes;
- required tests/evidence.

Each line becomes one clause. The full eight-dimension contract remains available through the API so more specialized or generated task creation can use it without expanding the default UI into a requirements form.

## API example

```json
{
  "project_id": 1,
  "title": "Add bounded cache support",
  "description": "Cache parsed configuration without changing callers.",
  "engineering_contract": {
    "required_behavior": ["Repeated reads reuse the parsed configuration"],
    "allowed_scope": ["backend/app/config", "backend/tests"],
    "forbidden_changes": ["Do not change the public settings API"],
    "architecture_constraints": ["No process-global mutable singleton"],
    "required_tests": ["uv run pytest tests/test_config.py"],
    "performance_requirements": ["Second read performs no file IO"],
    "compatibility_requirements": ["Existing Settings callers remain valid"],
    "acceptance_criteria": ["Existing tests and new cache tests pass"]
  }
}
```

## Current boundary

WP4 establishes the structured contract and makes it operational in role context and review. It does not yet turn every natural-language clause into a deterministic machine assertion. Repository tests, qualification checks, and future policy-specific validators should enforce clauses that can be measured mechanically; the Reviewer handles clauses that still require engineering judgement.
