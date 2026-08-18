# Project Policy / Engineering Contract

A project's declared engineering invariants — protected paths, required review
checks, architecture invariants, and the other categories below — as a
structured, enforceable contract distinct from the free-text background
reading SceneWorks already gave every role.

Status: **implemented, unit tested (32 tests in `backend/tests/test_policy.py`),
and proven at the workflow level by a qualification scenario**
(`policy-violation`). Not live-model validated.

No PCS-specific code exists anywhere in SceneWorks. PCS appears in this
document exactly once, as an example of what a project *could* declare with
this generic abstraction — the same way any other managed project could.

## What existed before this

Before WP4, a project had exactly one context mechanism:
`Project.architecture_context_paths` plus a fixed default list
(`AGENTS.md`, `ARCHITECTURE.md`, `CONTRIBUTING.md`, `ROADMAP.md`), read fresh
from the commit-pinned worktree and injected into every role's prompt as
*"Project context files (authoritative reference)"* — prose, undifferentiated
from a README. `test_commands` and `build_commands` existed as structured
lists but were only ever displayed, never checked. Nothing distinguished "the
Engineer should be aware of this" from "a violation of this must change the
review verdict," and nothing checked compliance independently of whatever the
Engineer itself claimed.

## Design

### Storage: structured fields plus repository-owned files

One `ProjectPolicy` row per project (`backend/app/models.py`), migration
`0003`. Eight categories, each a list of free-text statements except
`protected_paths`:

| Field | Enforcement | Category |
| --- | --- | --- |
| `protected_paths` | **Deterministic** — SceneWorks checks this itself | files/dirs that must not be hand-edited (generated files, frozen public API surface) |
| `architecture_invariants` | LLM-judged by the Reviewer | dependency-direction rules, layering rules |
| `forbidden_dependency_directions` | LLM-judged | e.g. "ui must not import from db" |
| `documentation_requirements` | LLM-judged | "changes to X must update docs/Y.md" |
| `performance_constraints` | LLM-judged | latency/throughput budgets |
| `required_review_checks` | LLM-judged, but named individually | a checklist the Reviewer must explicitly confirm |
| `go_no_go_commands` | informational (surfaced, not run automatically) | the release-qualification suite, distinct from routine `test_commands` |
| `release_requirements` | LLM-judged | changelog, version bump, sign-off conditions |

Plus `policy_file_paths`: repository-owned files (default candidates
`SCENEWORKS.md`, `docs/project-policy.md` — suggested, never forced), read the
same way `architecture_context_paths` already was, but rendered into their own
labelled block rather than mixed into general context.

**Why one table with typed categories rather than free-form key/value pairs.**
The roadmap lists genuinely different kinds of rule (a path pattern is not the
same shape as a performance budget), and a Reviewer prompt that has to parse an
untyped blob to find "is there a protected-paths rule in here somewhere" is
exactly the kind of ambiguity that lets a violation slip through. Typed fields
mean the deterministic check has something concrete to iterate over, and the
render function can label each category so nothing is ambiguous about what
kind of rule it's reading.

### Deterministic checking, only where it can be deterministic

`protected_paths` is checked by SceneWorks itself
(`backend/app/services/policy_check.py`, `check_protected_paths`): a pure
function, no I/O, matched via `fnmatch.fnmatchcase` (case-sensitive,
platform-independent — `fnmatch.fnmatch`'s case-folding differs between
Windows and Linux, which would make the same policy match differently in CI
than in local development) against the changed-file list `git diff --name-only`
already produces for the Reviewer node. `*` matches across `/`, so
`generated/*` protects everything under `generated/`, not just its direct
children.

Every other category remains LLM-judged: SceneWorks cannot mechanically verify
"the changelog was updated appropriately" the way it can verify "was this exact
path touched." Pretending otherwise would be exactly the false precision this
whole roadmap exists to avoid.

**Why this split, not "make the Reviewer check everything" or "make everything
deterministic."** The roadmap is explicit: *"The Engineer must not be
responsible for defining the criteria used to approve its own work."* A
deterministic check that runs regardless of what any agent claims is the
strongest form of that guarantee for the one category where it's achievable.
For everything else, the Reviewer is still the enforcement point — but it is
handed the policy as a labelled, explicit contract, not left to notice it while
skimming background reading.

## Reaching every role consistently

Every prompt-building call site in `WorkflowManager` (`backend/app/workflows/
manager.py`) fetches the project's rendered policy and passes it into
`PromptBuilder.build()`. This covers Product, CTO, Technical Expert, Architect,
Engineer and Reviewer — the full set the roadmap names — plus Triage, which
previously had its own separate, static prompt-building path
(`build_triage_prompt` was a `@staticmethod` taking only `(task, project)`; it
is now an instance method specifically so it can render policy the same way).
`CompanyService`'s manual "ask" path (`Ask CTO`, `Ask Architect`, ...) is wired
too, for the same reason: a human asking the CTO a question should not get a
different picture of project policy than the CTO gets inside a real task.

Verified directly, not just designed: `test_policy_reaches_every_named_role`
builds a real prompt for every role in `default_roles()` and asserts the
policy text is actually present in each one.

The rendered block is labelled distinctly from background context:

```text
# Project Policy (enforceable engineering contract)

## Protected paths (must not be hand-edited without explicit authorisation)
- check.py

This policy is an engineering contract, not background reading. It applies
to you the same way it applies to every other role working on this project.

You are the enforcement point for this policy. [Reviewer only] The Engineer
is not responsible for judging its own compliance with it. Check the
implementation against every item above; if anything was violated, the
verdict must be CHANGES_REQUESTED and must name the specific item.
```

The Reviewer-only enforcement sentence is a real, tested asymmetry
(`test_reviewer_gets_the_enforcement_instruction_others_do_not`), not just a
claim in a docstring.

An unconfigured project adds nothing: `test_no_policy_configured_adds_nothing_
to_the_prompt` asserts the block is entirely absent, not present-and-empty.

## The deterministic check in the Reviewer node

`_prepare_and_start_reviewer` (manager.py) already computes the Engineer's
diff and commit list for the Reviewer's prompt. WP4 adds one more step: it
also asks Git which files changed (`GitWorktreeService.changed_files`, the
same authoritative-evidence method qualification uses) and matches them
against `protected_paths`. A match is injected as its own `extra` block:

```text
# Policy Violations Detected By SceneWorks (verify and act on these)

The following files were modified and match a path this project has
declared protected. This was computed directly from the Git diff, not
inferred by any agent. Unless the approved architecture explicitly
authorised touching these paths, this is a policy violation and the
review verdict must be CHANGES_REQUESTED, naming each one:

- `check.py` matches protected pattern `check.py`
```

and recorded as a `policy.violation_detected` event with the exact paths and
patterns — evidence that survives independently of what the Reviewer's model
ultimately says.

## Proof: a violation detected during review

`evaluation/scenarios.py`, scenario `policy-violation`. A reference project
(the `calc-buggy` qualification fixture) declares `check.py` protected. The
scripted Engineer takes the shortcut the fixture's own `AGENTS.md` already
warns against — instead of fixing the real bug in `calc/core.py`, it removes
every check in `check.py` that would still fail against the unfixed code, so
the tampered suite genuinely reports **"all checks passed."** The scripted
Reviewer is told to approve.

This is deliberately the hard case, not the easy one: a harness that only
asked "did the checks pass" would be fooled exactly as a human skimming a
green CI run would be. The check that matters:

```text
[PASS] policy-violation — Project policy: a protected-path violation is
       detected during review
  ok   verification.tests_at_result   expected=True  actual=True
  ok   policy.violation_detected      expected={check.py} actual={check.py}
```

`review_verdicts` for this run is `['APPROVED']` — a false approval, scripted
deliberately, the same way `intentional-regression` scripts one. The violation
is caught anyway, because detection reads `policy.violation_detected` (what
SceneWorks itself found), never the Reviewer's verdict text. A mutation test,
`test_policy_violation_scenario_fails_if_the_protected_path_is_not_configured`,
removes `check.py` from the seeded policy and asserts the scenario **fails** —
proving the passing case is evidence of real detection, not a scenario that
would pass regardless of whether the mechanism worked.

`policy-violation` is in the required set (`REQUIRED_KEYS`) and the fast CI
smoke subset (`SMOKE_KEYS`) — a policy-detection regression is caught on every
push, not just at release time.

## API

| Endpoint | Purpose |
| --- | --- |
| `GET /api/projects/{id}/policy` | current policy; empty (never 404) for an unconfigured project, matching how `test_commands` defaults to `[]` |
| `PUT /api/projects/{id}/policy` | full replace — a policy is the project's current contract, not an accumulating log |
| `DELETE /api/projects/{id}/policy` | remove the policy row entirely (distinct from `PUT` with empty lists) |

## Worked example: PCS-shaped policy

PCS is not referenced anywhere in SceneWorks' source. This is a `PUT` body
showing the categories in the roadmap's own example ("frozen public Python
API, compatibility expectations, smoke/go-no-go suite, required documentation
updates, architectural invariants, performance constraints, release
qualification") expressed with the generic fields above — nothing here is
PCS-specific mechanism, only example content:

```json
{
  "protected_paths": [
    "pcs/api/public/*",
    "pcs/generated/*"
  ],
  "architecture_invariants": [
    "pcs.ui must not import from pcs.storage directly; go through pcs.api",
    "plugins may depend on pcs.api but pcs.api must not depend on any plugin"
  ],
  "forbidden_dependency_directions": [
    "pcs.core must not import from pcs.ui"
  ],
  "documentation_requirements": [
    "a change to pcs/api/public/* must update docs/api-reference.md in the same task"
  ],
  "performance_constraints": [
    "point cloud load for a 10M-point file must stay under 2s on the reference machine"
  ],
  "required_review_checks": [
    "confirm the public API's method signatures are unchanged, or the change is documented as a deliberate break",
    "confirm the smoke suite was run and referenced in the review"
  ],
  "go_no_go_commands": [
    "python -m pytest tests/smoke -q"
  ],
  "release_requirements": [
    "CHANGELOG.md updated",
    "version bumped per semver rules"
  ],
  "policy_file_paths": ["docs/pcs-engineering-contract.md"]
}
```

## What this does not do (deliberately)

- **No frontend UI.** Configuration is API-only for now
  (`GET`/`PUT`/`DELETE`). The roadmap's UI guidance ("extend the
  conversation-first model, not resource-centric CRUD screens") belongs to
  WP5's scope, not WP4's; adding a CRUD policy-editing screen now would be
  exactly the resource-centric pattern the roadmap says not to return to. A
  human can configure a policy via the API or `curl` today.
- **`go_no_go_commands` are not run automatically.** They are surfaced to
  roles as the release-qualification suite; nothing in WP4 executes them.
  Running them is release-workflow scope, not policy-declaration scope.
- **No cross-project policy templates or inheritance.** One policy per
  project, declared in full each time. If a real need for shared policy
  fragments across projects emerges, that is future work with its own
  evidence, not solved speculatively here.
