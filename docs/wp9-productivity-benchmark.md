# WP9 — Productivity benchmark

WP9 answers a different question from release qualification:

> Does the SceneWorks control plane improve engineering outcomes enough to justify its extra orchestration compared with using the coding agent directly?

Release qualification proves SceneWorks behaves correctly. The productivity benchmark measures engineering outcomes on real, pinned repository tasks.

## Experimental design

Each benchmark task is resolved to one immutable Git commit. Two independent worktrees are created from that exact commit:

1. **SceneWorks** — the normal triage/advisory/Architect/Engineer/Reviewer workflow. The benchmark automatically crosses the architecture approval gate but records that as one human intervention.
2. **Direct** — one Engineer invocation using the same SceneWorks backend abstraction, Engineer role instructions, project context and WP8 `coding` model-profile resolution, but without triage, architecture, review or workflow orchestration.

Using the same backend adapter and Engineer instructions is intentional. It isolates the value of the control plane rather than comparing unrelated prompts or provider integrations.

Repeats alternate execution order to reduce systematic provider/time-of-run bias.

## What counts as success

The model does not grade itself. After each run, the harness independently collects:

- repository-owned verification command results;
- Git-authoritative changed files;
- required changed-file patterns;
- forbidden changed-file patterns;
- result commit;
- elapsed time;
- human approval gates crossed;
- number of agent executions;
- Architect / Engineer / Reviewer execution counts;
- review-repair iterations;
- backend failures;
- concrete backend / model-profile / model for each execution.

A direct trial passes only when all verification commands pass, every required file pattern was touched, and no forbidden file was touched. A SceneWorks trial must satisfy those same independent acceptance gates **and** complete its own workflow at `READY_FOR_HUMAN`. Code that happens to pass tests while SceneWorks itself ends `FAILED`, `CANCELLED`, or stuck in `CHANGES_REQUESTED` is therefore a SceneWorks failure, not a success.

A failed implementation is **valid benchmark evidence**. A backend that was unavailable, an unusable repository, timeout, or invalid benchmark precondition is **BLOCKED** and makes the benchmark `INCOMPLETE` rather than being counted as a loss.

Speed is compared only when both paired trials pass. A fast wrong answer can never beat a slower correct answer.

## Baseline precondition

Every task declares one of:

- `must_fail` — verification must fail at the base commit. Recommended for bug fixes and features with a regression/acceptance test.
- `must_pass` — verification must pass at the base commit. Useful for refactors or performance work where behavior must remain healthy.
- `any` — no baseline truth assertion. Use sparingly and rely on strong file/acceptance constraints.

The default is `must_fail`. This prevents accidentally benchmarking a task that is already solved.

## Manifest

The benchmark is JSON and intentionally contains no provider-specific model IDs. WP8 resolves role profiles to the configured concrete model and the report records that selection.

```json
{
  "schema_version": 1,
  "name": "PCS historical tasks",
  "backend": "gemini_acp",
  "repeats": 3,
  "tasks": [
    {
      "key": "historical-bug-001",
      "title": "Fix historical regression",
      "description": "Exact task description that was given when the issue was originally solved.",
      "repository_path": "/absolute/path/to/pcs",
      "base_ref": "<commit-before-the-fix>",
      "verification_commands": ["<acceptance command that distinguishes the fix>"],
      "baseline_expectation": "must_fail",
      "architecture_context_paths": ["AGENTS.md"],
      "expected_changed_files": ["src/relevant/**"],
      "forbidden_changed_files": ["tests/golden/**"],
      "engineering_contract": {
        "required_behavior": ["Historical required behavior"],
        "forbidden_changes": ["Do not weaken the acceptance test"],
        "required_tests": ["Run the benchmark verification command"]
      }
    }
  ]
}
```

Validate without running agents:

```bash
cd backend
uv run python -m benchmarking --manifest ../benchmarks/pcs.json --validate
```

Run paired trials and preserve machine-readable evidence:

```bash
uv run python -m benchmarking \
  --manifest ../benchmarks/pcs.json \
  --json ../benchmark-results/pcs-run.json
```

Use `--mode sceneworks` or `--mode direct` for diagnostic runs. A real comparison should normally use `both`.

## PCS benchmark set

Do **not** benchmark open roadmap work first. Start with 5–10 historical PCS tasks whose correct result is already known:

- a localized bug fix;
- a small feature;
- a multi-file/API change;
- a refactor with preserved behavior;
- a performance/robustness task with an executable threshold.

For each task, pin the commit immediately before the historical fix and use an acceptance command that is independent of the agent's own claims. This gives us a known answer without leaking the historical patch to either arm.

Once the harness has stable data on PCS, repeat the same method on an unfamiliar second repository. That is the generalization test for hidden PCS-specific assumptions.

## Metrics deliberately unsupported today

WP9 does not fabricate metrics SceneWorks cannot currently attribute:

- provider-neutral token usage;
- monetary cost;
- architecture usefulness as a numeric score.

The report includes these as unsupported with reasons. Token/cost becomes measurable only after `AgentBackend` exposes attributable usage for the concrete WP8 model. Architecture usefulness needs a defined human rubric or independently qualified judge.

## Interpreting the report

The primary comparison is **success rate under identical acceptance gates**. Latency is secondary and is calculated only for paired successes. Human-intervention and execution-count deltas explain the control-plane overhead.

The benchmark should not be used to tune SceneWorks against the same small task set indefinitely. Keep a held-out task set, add new historical tasks over time, and periodically run an unfamiliar repository to reduce benchmark overfitting.
