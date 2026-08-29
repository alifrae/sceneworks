# Verification and Issue Traceability

## Current status

WP21 implements the verification/routing follow-up that remained after WP14-WP20.

SceneWorks now projects existing engineering evidence into a first-class task **Verification** view without creating another evidence database. Accepted `bug`, `feature`, and `idea` work items also receive an immutable `task.resolution` lifecycle snapshot containing the diagnosis/change claims plus SceneWorks-derived Git and verification facts.

The routing chain is now editable end to end:

```text
Role -> provider-neutral profile -> backend/model
```

The first edge is configured in Settings as an optional role profile override. The second edge remains centralized in model-profile routes.

## Verification authority

Verification intentionally distinguishes deterministic observations from engineering claims.

| Status | Meaning |
|---|---|
| `PASS` | SceneWorks can point to objective captured evidence that satisfies the check. |
| `FAIL` | Objective evidence contradicts the requirement/check, a required command failed, or deterministic scope/policy evidence failed. |
| `UNVERIFIABLE` | The requirement exists but SceneWorks has no deterministic verifier or attributable evidence mapping. |
| `NOT_APPLICABLE` | No requirement exists for that category. |

A Reviewer `APPROVED` verdict is shown as the independent review boundary. It **does not** convert an otherwise unverifiable acceptance criterion or test into PASS.

Overall result is conservative:

- `FAIL` if any required material check has objective failure evidence or the final Reviewer requests changes;
- `PASS` only when every required material check is objectively verified and the independent review boundary is satisfied when implementation occurred;
- `UNVERIFIABLE` when there is no objective failure but at least one required material check lacks a verifier/evidence mapping.

## Evidence reused

The projection reads existing authoritative stores:

1. `Task.engineering_contract`
   - acceptance criteria;
   - required tests;
   - allowed scope.
2. `Project.engineering_policy`
   - protected paths;
   - go/no-go commands;
   - semantic project constraints.
3. Git provenance
   - base/result commit;
   - captured changed files.
4. WP15 `EngineeringEvidence`
   - command/process/Git/verification evidence correlated to the task.
5. WP16 PCS verification and later evidence categories when they expose deterministic pass/fail semantics.
6. Reviewer result as an independent review decision, kept separate from objective criterion evidence.

No new verification ledger is introduced.

## Acceptance criteria

Acceptance criteria remain free-form text, so SceneWorks does **not** guess which observation proves which criterion.

The task contract order assigns stable projection identifiers:

```text
AC1
AC2
AC3
...
```

Advanced MCP `sceneworks.command.run` accepts optional explicit `criterion_ids` such as `AC1`. Those ids are stored inside the normal command evidence. The verification projection then uses the mapped objective command result.

Example:

```json
{
  "command": "python",
  "args": ["-m", "pytest", "tests/test_seek.py"],
  "criterion_ids": ["AC2"]
}
```

A mapped command with exit code `0` can verify AC2. A nonzero exit code fails AC2. A mapped screenshot observation without a criterion-specific deterministic threshold remains `UNVERIFIABLE`: the mapping alone does not manufacture pass semantics.

MCP also exposes:

```text
sceneworks.get_task_verification
```

in every MCP mode.

## Required tests

Required tests are mechanically stronger because the task contract stores executable command strings.

For each `required_tests` command, SceneWorks searches task-bound command evidence for the exact normalized command. The latest matching observation determines the current projection:

- exit `0` -> `PASS`;
- nonzero/failed evidence -> `FAIL`;
- no attributable exact command -> `UNVERIFIABLE`.

SceneWorks does not infer a passing test from an Engineer or Reviewer sentence such as "tests passed".

## Scope and project policy

The following checks are deterministic today:

- captured changed paths are inside `allowed_scope`;
- project `protected_paths` were not touched, using the existing `check_protected_paths()` implementation;
- exact project `go_no_go_commands` have attributable command evidence.

Semantic clauses such as architecture invariants, dependency directions, documentation requirements, performance constraints, required review checks, and release requirements remain `UNVERIFIABLE` until a dedicated verifier exists. They are not superficially marked PASS merely because the Reviewer approved the task.

## Verification UI

The Work page now contains:

```text
Plan | Changes | Results | Verification | Activity | Advanced
```

The Verification tab shows:

- overall PASS / FAIL / UNVERIFIABLE;
- acceptance criteria with evidence references;
- required tests;
- allowed-scope/protected-path/project-policy checks;
- independent Reviewer result;
- counts of passed, failed, and unverifiable checks;
- the accepted issue Resolution snapshot when present.

The Results tab also surfaces the accepted Resolution summary for issue-type work.

## Issue Resolution snapshots

Issues remain ordinary SceneWorks Tasks with `work_item_type` of `bug`, `feature`, or `idea`; there is no second Jira-like issue model.

When one of these work items is accepted, the central workflow acceptance path appends one immutable `task.resolution` Event. The original task description is never overwritten.

Resolution structure:

```text
Resolution
Root cause        attributed Engineer claim
Change made       attributed Engineer claim
Resolved commit   SceneWorks/Git provenance
Changed files     SceneWorks/Git provenance
Verification      objective WP21 verification snapshot
Remaining risk    attributed Reviewer/Engineer claim
```

The Engineer prompt now requests a stable handoff:

```text
## Resolution record
### Root cause
### Change made
### Validation performed
### Remaining risk
```

This text remains a claim. SceneWorks independently fills commit/files and embeds the objective verification projection. Reviewer `Regression risk` is preferred for the remaining-risk claim when available.

Resolution capture is idempotent: once an accepted issue has a `task.resolution` event, subsequent capture attempts return the existing snapshot instead of rewriting history.

## Role routing

The Settings page now exposes both routing edges.

### Role -> profile

Each role has a code-defined default profile and an optional persisted override. For example:

```text
Engineer: coding -> research   (role override)
Reviewer: strongest            (role default)
```

Clearing the override restores the role default.

### Profile -> backend/model

The existing profile routes remain the only place where concrete provider/backend model selection is configured:

```text
strongest -> Gemini ACP / provider default
coding    -> OpenCode / provider-model
research  -> Gemini ACP / provider default
```

Settings shows each role's default profile, effective profile, resolved backend, resolved model, and routing source. Executions still persist their concrete resolution at creation time so later settings changes cannot rewrite execution provenance.

## Remaining boundary

WP21 deliberately does not claim universal semantic verification. Free-form acceptance criteria and project policy clauses require either:

- explicit mapping to evidence with deterministic semantics; or
- a dedicated verifier that defines what PASS means.

Until then the correct result is `UNVERIFIABLE`, not a model-generated PASS.
