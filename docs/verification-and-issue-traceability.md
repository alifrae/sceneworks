# Verification and Issue Traceability

## Purpose

SceneWorks already captures substantial engineering evidence, but the web task view does not yet synthesize it into criterion-level verification. WP20 also added a lightweight Issues view over existing Tasks; the next traceability improvement should update a completed issue with what was actually diagnosed, changed and verified without creating a Jira-like parallel domain model.

This document records the current state and the recommended follow-up.

## Current implementation status

| Original capability | Current status | Notes |
|---|---|---|
| Select default agent backend | Implemented | Settings exposes the default autonomous worker. |
| Backend health/status in Settings | Implemented | Backend availability/version/detail are shown. |
| Configure `strongest / coding / research` -> backend/model | Implemented | Persisted Settings override. |
| Role -> model-profile mapping | Partial | Defaults are still code configuration in `roles/definitions.py`; not editable in UI. |
| Show inherited/default routing | Mostly implemented | Profile/backend inheritance exists, but the UI does not show a complete per-role resolution chain. |
| Show resolved concrete model | Partial | Concrete model is persisted on `Execution`; Settings does not yet show the resolved target for every role. |
| Explicit no-silent-fallback policy | Implemented | Provider changes after mutation require an explicit decision. |
| First-class Verification task tab | Missing | Task tabs are currently Plan / Changes / Results / Activity / Advanced. |
| Acceptance criterion -> evidence -> result | Missing synthesis | Contract + evidence exist, but there is no criterion-level projection. |
| Required-tests PASS/FAIL presentation | Missing synthesis | Tests may exist in workflow/evidence, but are not projected against `required_tests`. |
| Project-policy/scope compliance presentation | Missing synthesis | Policy and Git changed-file evidence exist separately. |
| Overall PASS / FAIL / UNVERIFIABLE | Missing | Reviewer verdict is currently the primary visible result. |

The important conclusion is: **do not build another evidence system**. The missing capability is a deterministic synthesis/projection over existing contracts, Git provenance, workflow events and EngineeringEvidence.

## Verification UX design

Add a **Verification** tab to each task.

Recommended structure:

```text
Overall
PASS / FAIL / UNVERIFIABLE

Acceptance criteria
AC1  VERIFIED       -> pytest test_seek -> exit 0 -> evidence #421
AC2  VERIFIED       -> PCS runbook step -> evidence #437
AC3  UNVERIFIABLE   -> no objective verifier/evidence mapping

Required tests
test_playback.py    PASS -> command/evidence reference
pytest test_seek    PASS -> command/evidence reference

Scope / policy
allowed scope       PASS
protected paths     PASS
required check X    UNVERIFIABLE

Reviewer
APPROVED

Summary
2 verified · 1 unverifiable · 0 failed
```

### Status semantics

- **VERIFIED/PASS** — SceneWorks can point to objective captured evidence that satisfies the check.
- **FAIL** — objective evidence contradicts the requirement/check, a required command failed, or a scope/policy violation is observed.
- **UNVERIFIABLE** — the requirement exists but SceneWorks has no deterministic verifier or no evidence was captured for it.
- **NOT APPLICABLE** — no requirement exists for that category.

A Reviewer `APPROVED` verdict must not silently convert an otherwise unverifiable criterion to PASS. Reviewer prose is an inference/review claim, not evidence.

## Evidence sources to reuse

The projection should use existing authoritative stores:

1. `Task.engineering_contract`
   - acceptance criteria;
   - required tests;
   - allowed scope;
   - forbidden changes/architecture/compatibility/performance constraints.
2. `Project.engineering_policy`
   - protected paths;
   - architecture invariants;
   - required review checks;
   - go/no-go commands/release requirements.
3. Git provenance
   - base/result commit;
   - changed files;
   - diff/hash evidence.
4. Workflow Events / Executions
   - command/test/tool outcomes when structured and attributable.
5. WP15 `EngineeringEvidence`
   - command/process/Git/task-correlated evidence.
6. WP16 PCS verification
   - deterministic runbook/health/runtime evidence.
7. WP17/WP18 GUI evidence
   - screenshot hashes, deterministic visual comparisons and governed UI actions.

Do not infer a passing test from an implementation summary that merely says "tests passed" if the command/result evidence is unavailable.

## Criterion mapping

Acceptance criteria are currently free-form strings. Therefore criterion-level mapping should be explicit rather than guessed by keyword similarity.

A future verifier/evidence record should be able to carry:

```json
{
  "criterion_id": "AC2",
  "status": "VERIFIED",
  "evidence_refs": [437, 441],
  "verifier": "pcs.run_verification"
}
```

The UI can then resolve `AC2` to the immutable criterion text stored on the task contract.

If an agent proposes `AC2 -> evidence #437`, SceneWorks may validate that the referenced evidence exists/succeeded, but the semantic claim that this evidence proves AC2 remains an engineering assertion unless the verifier itself has deterministic semantics for that criterion. The UI should expose that distinction when relevant.

## Required-test verification

Required tests are stronger because the contract already stores executable command strings.

For each configured required test, the synthesis should locate attributable command/test evidence and show:

- exact configured command;
- observed command/equivalent verifier;
- exit/result status;
- execution/evidence reference;
- timestamp/commit/session scope.

Do not run expensive repository-wide validation every time the tab is opened. Verification execution and verification presentation are separate concerns.

## Scope and project-policy checks

Several checks can be deterministic without a model:

- changed paths are within `allowed_scope`;
- protected paths were not touched;
- required go/no-go commands have captured results;
- base/result commit and changed-file provenance are available;
- PCS runbook steps passed/failed;
- known required review checks have evidence or are explicitly unverifiable.

Do not treat prose constraints such as architectural invariants as mechanically verified unless a dedicated verifier exists.

## Overall result

Recommended conservative rule:

- `FAIL` if any required criterion/test/policy check has objective failure evidence.
- `PASS` only when every required material check is objectively verified and the independent review boundary is satisfied.
- `UNVERIFIABLE` when there are no objective failures but one or more required checks lack a verifier/evidence mapping.

That makes "unverifiable" a first-class honest outcome rather than hiding it inside reviewer prose.

## Should agents update issue tickets with root cause and fix?

**Yes, but not as unqualified truth.**

The existing WP20 Issues page should remain a view over the Task domain. A completed `bug`, `feature`, or `idea` should gain a compact **Resolution** snapshot rather than creating a second ticket record.

Recommended snapshot:

```text
Resolution
Root cause        <engineering claim + provenance>
Change made       <SceneWorks/Git-derived commit + concise agent summary>
Verification      PASS / FAIL / UNVERIFIABLE + evidence refs
Changed files     <SceneWorks Git provenance>
Remaining risk    <reviewer/engineer claim, clearly identified>
Resolved commit   <result commit>
```

### Authority of each field

| Field | Authority |
|---|---|
| Root cause | Usually an Engineer/Reviewer claim; evidence references should accompany it where possible. |
| Change made | Git diff/commit is authoritative; prose summary is descriptive. |
| Verification | SceneWorks verification synthesis should be authoritative for captured checks. |
| Changed files / commit | Git provenance. |
| Remaining risk | Reviewer/Engineer inference; useful but not evidence. |

The agent should therefore **update the work item**, but SceneWorks should re-derive commit/files/verification from its own stores rather than trusting copied model text.

## Recommended automatic workflow

For issue-type work:

1. Engineer reports a structured resolution section at the end of implementation:
   - root cause;
   - fix/change made;
   - validation performed;
   - remaining concern.
2. Reviewer independently verifies and reports regression/unverified areas.
3. SceneWorks captures Git provenance and objective verification evidence.
4. When the human accepts the task, SceneWorks writes one durable `issue.resolution` snapshot referencing the accepted commit/evidence.
5. The Issues page/task Results or Verification tab shows that snapshot.

Do **not** overwrite the original issue description. The resolution is an appended lifecycle record.

## Routing polish

The other worthwhile small follow-up is editable role -> model-profile mapping.

The current two-level model is already good:

```text
Role -> provider-neutral profile -> backend/model
```

Do not replace it with per-role raw provider model IDs. Instead make the first edge editable while keeping provider identifiers centralized in profile routing.

Recommended Settings table:

| Role | Default profile | Effective profile | Backend | Model | Source |
|---|---|---|---|---|---|
| Architect | strongest | strongest | Gemini ACP | provider default | inherited |
| Engineer | coding | coding | OpenCode | provider/model | profile route |
| Reviewer | strongest | strongest | Gemini ACP | provider default | inherited |

A role override should be a profile override, not a duplicated backend/model override.

## Priority

Recommended implementation order:

1. **Verification synthesis + Verification tab** — highest value; converts existing evidence into an actionable engineering decision surface.
2. **Issue resolution snapshot** — small once verification synthesis exists and materially improves historical traceability.
3. **Role -> profile Settings override + resolved-route display** — useful polish, but lower engineering value than verification.

These are refinements of the existing architecture, not reasons to add another orchestration framework, evidence database, or ticketing subsystem.