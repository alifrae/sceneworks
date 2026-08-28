# WP12 — PCS Pilot & Adoption Gate

## Purpose

WP12 answers the question that qualification alone cannot answer:

> Is SceneWorks good enough to become the default control plane for real PCS engineering work?

WP9 built the paired productivity benchmark. WP12 turns that benchmark into an
explicit adoption decision and then requires a small real PCS dogfood pilot
before broader use.

WP12 is deliberately evidence-driven. A green SceneWorks CI suite proves the
control plane is internally healthy; it does **not** prove that using SceneWorks
is better than sending the same PCS task directly to the same coding agent.

## Sequence

```text
historical PCS corpus
        ↓
3 paired repeats / task
        ↓
SceneWorks vs direct worker
        ↓
independent verification + Git evidence
        ↓
WP12 adoption gate
        ↓
READY_FOR_PILOT ?
   │ yes                 │ no
   ▼                     ▼
2–3 low-risk PCS       diagnose failures
real dogfood tasks     define next WP(s)
   ↓
human review + merge
   ↓
ADOPT / DO NOT ADOPT
```

The gate is not a release gate and does not replace SceneWorks qualification.

## Phase A — historical PCS corpus

Create `benchmarks/pcs.json` from `benchmarks/pcs.template.json` on the machine
that has the current PCS repository checkout.

The corpus must contain **5–10 closed historical PCS tasks**. Use a mix of:

- localized bug fix;
- small feature;
- multi-file/API change;
- refactor with preserved behavior;
- performance or robustness change with an executable threshold.

Each case must have:

1. the historical task description without the known implementation patch;
2. a Git base representing the broken/pre-change state;
3. deterministic verification commands;
4. a baseline expectation proving the case is valid;
5. scope/file constraints where they are objectively known;
6. `historical_fix_ref` / source metadata for audit only.

`historical_fix_ref` is never used by the benchmark runner to construct the
agent prompt. It exists so a human can trace the benchmark case back to the
known historical answer.

### Portable PCS path

The checked-in manifest should use:

```json
"repository_path": "${PCS_REPO}"
```

Set the environment variable on the benchmark machine.

PowerShell:

```powershell
$env:PCS_REPO = "C:\Workspace\repos\GitHub\point_cloud_studio\point_cloud_studio"
```

Bash:

```bash
export PCS_REPO=/path/to/point_cloud_studio
```

SceneWorks expands the repository path only. It deliberately does not expand
shell variables in verification commands while parsing the manifest.

## Acceptance-test integrity

Do not benchmark a historical fix merely by checking that the historical patch
can be rediscovered. The acceptance oracle must distinguish correct behavior
without exposing the implementation.

Preferred order:

1. use a regression/acceptance test that already existed before the historical fix;
2. use an external deterministic command that exercises the broken behavior;
3. if the regression test was introduced in the known fix, create a **benchmark-only seed commit** containing the test but not the implementation fix, then use that seed commit as `base_ref`.

Example seed procedure:

```bash
git switch --detach <commit-before-fix>
git switch -c benchmark/<case-key>-seed
git checkout <historical-fix-ref> -- tests/<regression-test-file>
git add tests/<regression-test-file>
git commit -m "benchmark: add acceptance oracle for <case-key>"
```

Then prove:

```bash
<verification command>   # must fail on the benchmark seed
```

The seed branch is benchmark infrastructure only. It must never be merged into
PCS release branches.

This approach lets both arms see the same acceptance test while withholding the
known implementation patch.

## Phase B — paired benchmark

For each task WP12 runs:

```text
same PCS base commit
same task description
same engineering contract
same backend
same model-profile resolution
same executable acceptance criteria
```

against two independent worktrees:

- **SceneWorks**: triage/advisers/optional Architect → Engineer → Reviewer;
- **Direct**: one Engineer invocation through the same backend abstraction.

Run **three repeats**. Execution order alternates between repeats to reduce
systematic time/provider bias.

Run:

```bash
cd backend
uv run python -m benchmarking \
  --manifest ../benchmarks/pcs.json \
  --adoption-gate \
  --json ../benchmark-results/pcs-adoption.json
```

On PowerShell the same command can be written on one line.

Exit codes:

| Code | Meaning |
|---:|---|
| `0` | benchmark complete and adoption gate passed |
| `2` | benchmark incomplete / blocked evidence |
| `3` | evidence complete but adoption gate not met |
| `4` | invalid manifest / usage |

## Adoption policy

The initial PCS policy is explicit in the manifest rather than hard-coded into
workflow behavior:

```json
{
  "min_tasks": 5,
  "min_repeats": 3,
  "require_complete_pairs": true,
  "min_sceneworks_success_rate": 0.8,
  "max_success_rate_regression": 0.0,
  "max_median_time_ratio": 2.0,
  "max_mean_human_interventions": 1.0,
  "max_backend_failures": 0
}
```

Interpretation:

- at least five independent historical PCS cases;
- three paired repeats per case;
- no blocked/missing comparison arm;
- SceneWorks succeeds on at least 80% of measured trials;
- SceneWorks success rate is not lower than the direct-agent baseline;
- for pairs where both succeed, median elapsed time is at most 2× direct;
- average explicit human intervention is at most one per SceneWorks trial;
- no SceneWorks backend/control-plane execution failure.

The result is one of:

- `READY_FOR_PILOT` — all configured checks pass;
- `NOT_READY` — evidence is complete but one or more thresholds fail;
- `INSUFFICIENT_EVIDENCE` — corpus/repeats/pairs/metrics are incomplete.

A `COMPLETE` benchmark may therefore be `NOT_READY`. That distinction is the
main WP12 addition over WP9.

## What does not count as evidence

WP12 does not use:

- self-reported agent confidence;
- architecture text length;
- reviewer prose length;
- fabricated token/cost estimates;
- a single impressive task;
- a task whose acceptance command already passes at the base commit;
- a trial blocked by provider/setup failure counted as an engineering loss.

## Task context attachments

Real PCS debugging work often depends on screenshots, requirements PDFs, logs,
JSON/CSV excerpts, or similar context that cannot be represented faithfully by
a short task description. WP12 therefore includes first-class task attachments
before the dogfood pilot rather than evaluating a text-only workflow.

The V1 boundary is intentionally narrow:

- PNG/JPEG/WebP, PDF, TXT/Markdown, JSON and CSV only;
- attachment bytes are owned by SceneWorks under `data/attachments`, never
  copied into the managed repository or an agent worktree;
- metadata includes an immutable SHA-256 digest;
- attachments can be changed only while the task is `NEW`, freezing the context
  set before workflow execution begins;
- Gemini ACP receives images/resources through advertised prompt capabilities;
  missing binary capabilities fail explicitly rather than dropping context;
- text files have a safe text fallback for older ACP agents;
- attachment contents are labelled **untrusted user context**: instructions
  found inside a screenshot, PDF, log or Markdown file cannot override the role
  prompt, task request, engineering contract, or project policy;
- MCP exposes semantic attachment listing/retrieval in all modes and bounded
  upload in Standard/Advanced mode without exposing host filesystem paths;
- task deletion and project-history purge remove SceneWorks attachment metadata
  and storage without modifying the registered Git repository.

The complete storage, REST, MCP, ACP and trust-boundary contract is documented in
`docs/architecture/task-attachments.md`.

## Phase C — real PCS dogfood pilot

Only start this phase after `READY_FOR_PILOT`.

Select **2–3 low-risk real PCS tasks** that would have been implemented anyway.
Do not invent work solely to make SceneWorks look useful.

Good pilot tasks:

- bounded bug with regression test;
- small API/documentation-consistent feature;
- isolated robustness improvement.

Avoid for the first pilot:

- release-critical emergency fixes;
- broad architecture migrations;
- destructive repository operations;
- work requiring unavailable hardware or proprietary lab data for basic validation.

For each pilot task:

1. create the task through SceneWorks;
2. attach relevant screenshots/PDFs/log extracts before starting work when they
   materially improve the engineering context;
3. use the normal workflow and isolated worktree;
4. do not manually repair the worktree behind SceneWorks' back;
5. inspect the contract, attached-context provenance, actual diff, tests, review result and provenance;
6. record any human intervention not already represented in SceneWorks;
7. merge only after normal human review.

The pilot is successful only when at least two tasks are completed and manually
accepted without discovering a control-plane defect that makes SceneWorks less
safe or materially more burdensome than the existing workflow.

## Failure handling

A failed adoption check is not a reason to tune prompts blindly until the same
five cases pass.

Classify failures first:

| Failure | Follow-up |
|---|---|
| backend/process instability | agent backend / ACP operational WP |
| wrong task decomposition/routing | workflow/routing WP |
| poor engineering result in both arms | worker/model/task-definition problem, not SceneWorks value |
| direct passes, SceneWorks fails | control-plane regression; investigate before adoption |
| excessive latency with equal quality | simplify workflow / adaptive routing |
| repeated manual intervention | approval/contract/recovery UX problem |
| invalid or weak benchmark oracle | repair the benchmark case, do not tune SceneWorks to it |

New work packages should be derived from repeated failure classes, not from one
anecdotal run.

## Current repository status

The connected `alifrae/point_cloud_studio` GitHub repository contains useful
historical PCS fixes, including decoder/replay/viewer regressions, but it is an
archived older PCS repository rather than the current production checkout.
Therefore it is useful for discovering candidate historical cases, but the
final `pcs.json` and the actual paired runs should be built/validated against
the current local PCS history on the laptop.

This is expected: WP12 was always the first work package whose decisive evidence
comes from running SceneWorks against PCS rather than only testing SceneWorks
itself.

## Closure criteria

WP12 is closed only when all of the following are true:

- [x] adoption-gate data model and deterministic evaluator exist;
- [x] benchmark CLI can return a distinct gate-not-met status;
- [x] checked-in PCS template defines the initial policy;
- [x] repository path is portable through `${PCS_REPO}`;
- [x] unit tests cover READY / NOT_READY / INSUFFICIENT_EVIDENCE;
- [x] task attachments are bounded, provenance-hashed, MCP-visible and Gemini-ACP-capable before pilot use;
- [ ] `benchmarks/pcs.json` contains 5–10 validated historical PCS tasks;
- [ ] three paired repeats per task have been executed with the intended backend/model;
- [ ] machine-readable benchmark evidence is retained;
- [ ] adoption result is `READY_FOR_PILOT` or failures have been explicitly converted into follow-up WPs;
- [ ] if ready, 2–3 low-risk real PCS tasks have been dogfooded and manually reviewed/merged;
- [ ] final adopt/do-not-adopt decision is recorded.

Until the laptop-dependent items are complete, WP12 is **implemented but not
closed**.
