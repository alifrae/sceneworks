# Project engineering policy

SceneWorks has two complementary engineering constraint layers:

- **Project engineering policy** — long-lived invariants and guardrails that apply to every task in a project.
- **Task engineering contract** — the scope, required behavior, tests and acceptance criteria for one specific task.

The project policy is stored in `Project.engineering_policy` as structured JSON. This is the current-architecture port of the earlier WP4 `ProjectPolicy` prototype. A JSON column is used instead of the prototype's separate table because current SceneWorks already has a stable `Project` resource and Alembic revisions through `0006`; migration `0007` adds the policy without creating the prototype's conflicting second `0003` migration head.

## Policy fields

- `protected_paths` — repo-relative, case-sensitive shell-glob patterns that normal implementation work must not touch.
- `architecture_invariants` — system boundaries or architecture rules that all roles must preserve.
- `forbidden_dependency_directions` — dependency directions that must not be introduced.
- `documentation_requirements` — documentation obligations that apply across tasks.
- `performance_constraints` — durable performance constraints.
- `required_review_checks` — checks the Reviewer must account for.
- `go_no_go_commands` — project qualification commands that define release/go-no-go evidence.
- `release_requirements` — durable release constraints.
- `policy_file_paths` — repository-owned policy documents read from the same committed worktree snapshot as normal project context, but rendered as a separate binding policy block.

## API

The policy is managed independently of normal project metadata:

```text
GET    /api/projects/{project_id}/policy
PUT    /api/projects/{project_id}/policy
DELETE /api/projects/{project_id}/policy
```

`GET` is always answerable for an existing project. An unconfigured project returns empty lists for every policy field. `PUT` fully replaces the current policy. `DELETE` resets it to an empty policy.

Example:

```json
{
  "protected_paths": ["generated/*", "api/public/*"],
  "architecture_invariants": ["UI must not import persistence directly"],
  "forbidden_dependency_directions": ["domain -> web"],
  "documentation_requirements": ["Update architecture docs when boundaries change"],
  "performance_constraints": ["Do not add unbounded in-memory task history"],
  "required_review_checks": ["Check migration compatibility"],
  "go_no_go_commands": ["uv run python -m evaluation"],
  "release_requirements": ["Qualification suite passes"],
  "policy_file_paths": ["AGENTS.md", "docs/architecture.md"]
}
```

## Prompt authority

Every role receives the project policy as a distinct **binding** block. It is not mixed into free-text project background. Triage receives both project policy and the task engineering contract, and Engineer/Reviewer continue to receive the task-specific contract separately.

This separation is intentional: project policy answers "what must always remain true here?"; the task contract answers "what must this particular change do and where may it operate?"

## Deterministic protected-path enforcement

`protected_paths` is mechanically checkable and therefore is not delegated to the model.

For Reviewer execution, SceneWorks obtains changed files from persisted WP6 Git provenance when available; otherwise it derives the paths from the actual Git diff headers already produced for review. `check_protected_paths()` uses `fnmatch.fnmatchcase`, so matching is case-sensitive and consistent between Windows and Linux.

When a changed file matches a protected pattern:

1. the Reviewer prompt contains a stable `[SCENEWORKS_POLICY_VIOLATION]` marker plus the matching file/pattern evidence;
2. the workflow records a `policy.violation_detected` event;
3. the workflow forces the effective verdict to `CHANGES_REQUESTED`, even if the model returned `VERDICT: APPROVED`.

This preserves the important WP4 authority rule: the agent being reviewed cannot make a deterministic project-policy violation disappear by claiming that the implementation is acceptable.

## Current boundary

Only `protected_paths` has deterministic enforcement because it can be established directly from Git evidence without interpretation. Architecture, documentation, performance, dependency and release clauses remain explicit Reviewer obligations until a reliable project-specific validator exists for them. Do not add superficial mechanical checks simply to make those fields look automated.
