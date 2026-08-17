# Qualification and go/no-go

SceneWorks qualifies itself with a provider-independent **engineering outcome**
evaluation suite. It evaluates SceneWorks — routing, architecture gating,
implementation provenance, review lifecycle, repair convergence, cancellation and
restart behaviour — not its individual components, and not a model's talent.

Status: **implemented, unit tested, and self-tested** (30 tests in
`backend/tests/test_qualification.py`, 4 of them driving full workflows).
Live qualification mode is **implemented but not live-model validated** — see
[Live mode](#live-mode).

```bash
cd backend
uv run python -m evaluation              # full suite (19 scenarios, ~2.5-6 min)
uv run python -m evaluation --smoke      # CI subset (5 scenarios, ~60 s)
uv run python -m evaluation --list
uv run python -m evaluation --scenario bug-fix -v
uv run python -m evaluation --json qualification.json
```

Wall-clock varies widely: the suite creates 19 Git repositories, ~70 worktrees
and ~70 subprocesses back to back, and `git worktree add` on Windows degrades
sharply under that load. The per-scenario budget is 300 s for that reason
(a scenario takes ~13 s in isolation).

## What replaced what

The V2.5 framework in `backend/evaluation/` reported **8/8 PASS in 5.1 seconds**
while printing `implementation: INCORRECT` beside every one of those passes. It
never invoked a workflow: `passed` was computed as `len(self.errors) == 0`, so
"nothing was evaluated" and "everything was correct" produced the same verdict.
`routing_correct` was a literal `True`. The documented entry point
(`python -m evaluation.runner`) did not exist.

The recorded evidence is in [wp0-baseline-audit.md](wp0-baseline-audit.md), F1
and F2.

## Design rules

Each rule exists because the old framework broke it.

1. **A scenario passes only if it declares checks and every check passes.** A
   scenario that asserts nothing is `BLOCKED`, never `PASS`. Enforced
   structurally in `ScenarioResult.finalize()`, not by convention.
2. **Every metric is measured or explicitly not measured.** `None` means "not
   applicable to this scenario". A metric the harness cannot measure at all is
   named in `UNSUPPORTED_METRICS` with the reason and is never given a value.
3. **A suite cannot report PASS if required scenarios did not run.** A partial
   run (`--smoke`, `--scenario`) reports `BLOCKED`, so a pipeline cannot treat
   "we did not actually check" as success.
4. **Failure outranks blocked.** One wrong outcome fails the suite regardless of
   what else happened.

## How outcomes become real measurements

Two reference repositories are built from scratch as real Git repositories in a
temporary directory, and each ships **its own check suite** — a stdlib-only
`check.py` that exits non-zero when the code is wrong:

| Repo | State at base commit |
| --- | --- |
| `calc-buggy` | `total()` silently drops negative values; `check.py` **fails** |
| `calc-healthy` | `total()` correct; `check.py` **passes** |

The harness runs `check.py` at the base commit and again in the result worktree.
That is what makes `tests_passing` a measurement rather than an assumption: a bug
fix must move the repository from failing to passing, and a regression is
detected as passing to failing. A fixture whose declared state does not match
reality **blocks** the scenario, because "tests now pass" proves nothing if they
were never broken.

Everything else is read from authoritative sources rather than from agent prose:
changed files from `git diff --name-only`, commits from the task record, review
verdicts from the executions, routing from the events triage emitted.

## Scenarios

19 scenario classes, all required for a PASS verdict.

| Key | What it establishes |
| --- | --- |
| `architecture-investigation` | read-only analysis reaches a human without touching code |
| `bug-fix` | seeded defect is fixed and verified by the repository's own checks |
| `small-feature` | new module added, existing behaviour untouched |
| `multi-file-feature` | coordinated change across two files |
| `refactoring` | structure changes, behaviour preserved (checks pass before *and* after) |
| `api-modification` | signature extended without breaking callers |
| `intentional-regression` | **negative control** — a regression is detected |
| `reviewer-detects-defect` | reviewer requests changes on broken work |
| `reviewer-engineer-repair-loop` | repair cycles back through Engineer and converges |
| `ambiguous-requirement` | vague request routes through Product |
| `no-implementation-needed` | a decision question produces no code |
| `documentation-only` | only docs change; source files are forbidden |
| `performance-task` | routes through the Technical Expert |
| `failed-execution` | agent failure surfaces as FAILED with no commit |
| `cancellation` | cancellation stops the workflow, no commit |
| `restart-recovery` | restart reports what survived and what was interrupted |
| `unnecessary-change` | **negative control** — unrequested edits are detected |
| `incorrect-triage` | **negative control** — wrong routing is detected |
| `memory-injection` | accepted decisions reach the agent; proposals and irrelevant memories do not |

### Negative controls

Three scenarios seed a **wrong** outcome and pass only when the harness
*detects* it. They are what keeps the suite honest: if the harness stops seeing a
seeded regression, those scenarios fail and qualification blocks the release.
They are required for exactly that reason.

## Measured metrics

| Metric | Source |
| --- | --- |
| routing correctness, request type, implementation-required decision | triage events |
| advisory roles selected / actually executed | triage events + execution rows |
| files changed, expected-but-unchanged, unexpectedly changed | `git diff --name-only` |
| base commit, result commit, branch, diff size | task record + Git |
| tests passing at base / at result | the repository's own `check.py` |
| review verdicts, defect detection, false approval | execution results |
| repair iterations | engineer execution count |
| human interventions | actions the driver performed |
| backend failures | execution statuses |
| execution duration | wall clock |
| cancellation honoured | final task status after cancel |
| recovery: status before/after, interrupted executions, surviving worktree, retry behaviour | restart driver |
| memories injected, proposals withheld, retrieval query terms | `memory.injected` events |
| provenance: project, task, executions | database |

### Not measured — and why

Reported as unsupported rather than estimated:

- **`architecture_usefulness`** and **`requirement_quality`** — these need human
  or model judgement. With a scripted backend the analysis text is whatever the
  script says, so any score would measure the script, not SceneWorks. The suite
  measures `architecture_result_present` and `architecture_result_bytes` instead.
- **`cost_estimate`** — `AgentBackend` has no token or cost accounting. A
  currency figure would be fabricated.

`test_unsupported_metrics_are_named_with_reasons_not_scored` asserts no
`Observations` field quietly supplies one of these anyway.

## Exit codes

| Code | Verdict | Meaning |
| --- | --- | --- |
| 0 | `PASS` | every selected scenario passed and every required scenario was among them |
| 1 | `FAIL` | at least one scenario produced a wrong engineering outcome |
| 2 | `BLOCKED` | nothing failed, but qualification could not be completed — a required scenario was skipped, a scenario declared no checks, or the harness hit an environment problem |
| 3 | `NOT_RUN` | no scenario was executed |
| 4 | — | usage error |

Code 2 is the important one: a release pipeline must not read "we did not
actually check" as success.

## Machine-readable report

`--json PATH` writes a report with `schema: "sceneworks.qualification/1"`
containing the verdict, counts, required scenarios, missing required scenarios,
the unsupported-metric declarations, and for every scenario its checks
(`name`, `passed`, `expected`, `actual`, `detail`), full observations and
blockers.

## Qualifying a real agent backend

Separate from `--live` (which drives one task against a real repository), this
runs **qualification scenarios** against a real provider:

```bash
python -m evaluation --backend openhands --scenario bug-fix
python -m evaluation --backend openhands --live-subset   # provider subset
python -m evaluation --backend gemini_acp --scenario cancellation
```

Rules that keep a provider run honest:

- **An unusable provider is BLOCKED, never PASS.** The harness calls `health()`
  before the scenario and blocks with the health detail if unavailable.
- **Scripted-only scenarios are BLOCKED against a real backend.** Negative
  controls work by scripting a specific wrong behaviour, which a real model cannot
  be made to reproduce on cue. Four scenarios are marked `live_capable`:
  `architecture-investigation`, `no-implementation-needed`, `bug-fix`,
  `cancellation` — that is what `--live-subset` runs.
- **A live run declares no required scenarios**, so a live PASS cannot be mistaken
  for a release gate. Only the deterministic `fake` suite gates a release, and a
  partial `fake` run still reports BLOCKED.
- **Metrics that stop being measurable are declared unsupported**
  (`reviewer_false_approval`, `repair_iterations`) rather than asserted against a
  real model.
- The backend version and health detail are recorded per scenario, so a result is
  attributable to a concrete version and mode.

Results from WP2.5 (identical scenario, both real backends):

| | OpenHands 1.17.0 (`local`) | Gemini ACP 0.55.1 |
| --- | --- | --- |
| `cancellation` | PASS, 4/4 checks, 34.0 s | PASS, 4/4 checks, 12.7 s |
| final status | `CANCELLED` | `CANCELLED` |
| result commit | none | none |

See [wp2.5-openhands-validation.md](wp2.5-openhands-validation.md).

## Live mode

```bash
uv run python -m evaluation --live /path/to/repo [--live-commit COMMIT]
```

Optional, opt-in, and **not part of the automated suite** — the automated suite
must never depend on a private repository or a live model.

Live mode uses the configured agent backend against a real repository pinned to a
real commit. It **cannot** assert implementation correctness, because it has no
reference expectations; it records what happened for human assessment and
declares `implementation_correct` and `tests_pass_at_result` unsupported.

What it does assert is safety and provenance:

- the human clone is left on the same commit it started on;
- the human working tree is still clean;
- the analysed snapshot is recorded as a base commit;
- the task reached a defined state.

It refuses to run against a repository with uncommitted changes, because a dirty
tree makes the pinned commit unrepresentative of what is being qualified. It does
not auto-approve architecture on the human's behalf — human approval is one of
the invariants being demonstrated.

Live mode declares **no required scenarios**, so a live PASS can never be
mistaken for a release gate.

## Defects this suite has already found

Recorded here because they are the argument for the suite existing.

1. **`WorkflowManager.shutdown()` closed the checkpointer under running graphs.**
   In-flight graph tasks then wrote to a closed aiosqlite handle and died with
   `ValueError: no active connection` from inside LangGraph — an opaque error
   telling an operator nothing about what happened to their task. Fixed: graphs
   are cancelled before the connection closes.
2. **A shutdown-cancelled execution was recorded as `CANCELLED`.**
   Indistinguishable from a deliberate user cancellation, and invisible to
   restart reconciliation, which looks for interrupted work. Fixed: the engine
   records `INTERRUPTED` while shutting down.
3. **A task could be stranded in a running state forever.** After a *clean*
   shutdown the engine finalized its own executions, so restart reconciliation
   found no active execution and never reconciled the task — leaving it in
   `ARCHITECTURE_ANALYSIS` with no agent, no graph and no retry path, displayed
   in the UI as permanently working. Fixed: `recover_interrupted()` gained a
   second pass that reconciles tasks claiming to run with nothing running.
4. **Triage was skipped entirely for the `fake` backend**, so no automated test
   could observe a routing decision (WP0 F3). Fixed: the bypass is gone and the
   fake backend answers triage with scriptable JSON.
5. **The auto-repair loop was never actually counted.** The existing graph test
   asserted only the final status, so a repair loop that never cycled would have
   passed. Qualification measures engineer executions, and the convergent loop is
   now observed at 2 runs with the bound at 3.

## Self-tests

`backend/tests/test_qualification.py` tests the suite rather than SceneWorks —
because a qualification suite that cannot fail is worse than none.

The closure test is
`test_engineer_that_changes_nothing_makes_the_suite_fail`: it takes the passing
`bug-fix` scenario and replaces the Engineer with one that writes no code.
Nothing raises — the workflow runs, the reviewer approves, the task reaches
`READY_FOR_HUMAN`. The old framework would have reported PASS. Qualification
reports **FAIL**, on `verification.tests_at_result` and
`provenance.result_commit`.

Three more mutation tests follow the same pattern: wrong triage routing must be
detected, a seeded regression must still be caught by the negative control, and
`test_memory_injection_scenario_fails_when_the_decision_is_unaccepted` downgrades
the seeded decision from `accepted` to `proposed` and asserts the scenario fails —
proving the memory check observes real injection rather than the mere presence of
a memory row.
