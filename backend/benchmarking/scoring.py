"""Outcome scoring for productivity benchmarks and WP12 adoption gating.

Only deterministic evidence affects the per-trial quality gate: repository
verification commands plus Git-authoritative file provenance. Subjective
architecture or review quality is intentionally not converted into a fake
numeric score.
"""

from __future__ import annotations

import fnmatch
import statistics

from benchmarking.models import (
    AdoptionGatePolicy,
    AdoptionGateResult,
    AdoptionVerdict,
    BenchmarkManifest,
    BenchmarkReport,
    BenchmarkStatus,
    BenchmarkTask,
    GateCheck,
    ModeAggregate,
    PairComparison,
    TrialResult,
    TrialVerdict,
)


UNSUPPORTED_PRODUCTIVITY_METRICS: dict[str, str] = {
    "token_usage": (
        "AgentBackend/AgentResult does not yet expose provider-neutral token usage. "
        "A benchmark must not infer tokens from text length or provider logs."
    ),
    "monetary_cost": (
        "SceneWorks has no provider-neutral price/usage accounting contract yet. "
        "Cost remains unsupported until usage and pricing evidence are attributable "
        "to the concrete model recorded on each execution."
    ),
    "architecture_usefulness": (
        "Requires a human-scored rubric or an independently qualified judge. "
        "Presence or length of architecture text is not a usefulness score."
    ),
}


def match_file_constraints(
    task: BenchmarkTask, changed_files: list[str]
) -> tuple[list[str], list[str]]:
    """Return (missing expected patterns, changed forbidden files)."""
    normalized = [path.replace("\\", "/") for path in changed_files]
    missing = [
        pattern
        for pattern in task.expected_changed_files
        if not any(fnmatch.fnmatch(path, pattern) for path in normalized)
    ]
    forbidden = sorted(
        {
            path
            for path in normalized
            if any(fnmatch.fnmatch(path, pattern) for pattern in task.forbidden_changed_files)
        }
    )
    return missing, forbidden


def finalize_trial(trial: TrialResult, task: BenchmarkTask) -> TrialResult:
    """Compute a trial verdict from measured evidence.

    BLOCKED is reserved for a run that did not produce comparable evidence.
    FAIL is a valid engineering outcome and therefore remains usable benchmark
    data. A SceneWorks arm must also complete its control-plane lifecycle: code
    that happens to pass checks while the workflow itself failed is not a
    successful SceneWorks delivery.
    """
    trial.unsupported_metrics = dict(UNSUPPORTED_PRODUCTIVITY_METRICS)
    if trial.blocker:
        trial.verdict = TrialVerdict.BLOCKED
        trial.quality_gate_passed = None
        return trial

    trial.expected_files_missing, trial.forbidden_files_changed = (
        match_file_constraints(task, trial.files_changed)
    )
    verification_passed = bool(trial.verification) and all(
        result.passed for result in trial.verification
    )
    workflow_delivered = (
        trial.mode != "sceneworks" or trial.final_task_status == "READY_FOR_HUMAN"
    )
    trial.quality_gate_passed = (
        verification_passed
        and workflow_delivered
        and not trial.expected_files_missing
        and not trial.forbidden_files_changed
    )
    trial.verdict = TrialVerdict.PASS if trial.quality_gate_passed else TrialVerdict.FAIL
    return trial


def compare_pair(
    sceneworks: TrialResult | None,
    direct: TrialResult | None,
) -> PairComparison:
    if sceneworks is None and direct is None:  # pragma: no cover - caller invariant
        raise ValueError("at least one trial is required")
    sample = sceneworks or direct
    assert sample is not None

    if (
        sceneworks is None
        or direct is None
        or sceneworks.verdict is TrialVerdict.BLOCKED
        or direct.verdict is TrialVerdict.BLOCKED
    ):
        outcome = "incomplete"
    elif sceneworks.verdict is TrialVerdict.PASS and direct.verdict is TrialVerdict.PASS:
        outcome = "both_pass"
    elif sceneworks.verdict is TrialVerdict.PASS:
        outcome = "sceneworks_only_pass"
    elif direct.verdict is TrialVerdict.PASS:
        outcome = "direct_only_pass"
    else:
        outcome = "neither_pass"

    ratio = None
    if (
        sceneworks is not None
        and direct is not None
        and sceneworks.verdict is TrialVerdict.PASS
        and direct.verdict is TrialVerdict.PASS
        and direct.duration_seconds > 0
    ):
        ratio = round(sceneworks.duration_seconds / direct.duration_seconds, 4)

    return PairComparison(
        task_key=sample.task_key,
        repeat=sample.repeat,
        outcome=outcome,
        sceneworks_seconds=(sceneworks.duration_seconds if sceneworks else None),
        direct_seconds=(direct.duration_seconds if direct else None),
        elapsed_ratio_sceneworks_over_direct=ratio,
        human_intervention_delta=(
            sceneworks.human_interventions - direct.human_interventions
            if sceneworks is not None and direct is not None
            else None
        ),
        agent_execution_delta=(
            sceneworks.agent_executions - direct.agent_executions
            if sceneworks is not None and direct is not None
            else None
        ),
    )


def build_comparisons(trials: list[TrialResult]) -> list[PairComparison]:
    pairs: dict[tuple[str, int], dict[str, TrialResult]] = {}
    for trial in trials:
        pairs.setdefault((trial.task_key, trial.repeat), {})[trial.mode] = trial
    return [
        compare_pair(pair.get("sceneworks"), pair.get("direct"))
        for _, pair in sorted(pairs.items())
    ]


def build_aggregates(trials: list[TrialResult]) -> list[ModeAggregate]:
    aggregates: list[ModeAggregate] = []
    for mode in ("sceneworks", "direct"):
        selected = [trial for trial in trials if trial.mode == mode]
        if not selected:
            continue
        measured = [trial for trial in selected if trial.verdict is not TrialVerdict.BLOCKED]
        passed = [trial for trial in measured if trial.verdict is TrialVerdict.PASS]
        success_rate = round(len(passed) / len(measured), 4) if measured else None
        median_success = (
            round(statistics.median(t.duration_seconds for t in passed), 3)
            if passed
            else None
        )
        mean_human = (
            round(statistics.mean(t.human_interventions for t in measured), 3)
            if measured
            else None
        )
        mean_executions = (
            round(statistics.mean(t.agent_executions for t in measured), 3)
            if measured
            else None
        )
        aggregates.append(
            ModeAggregate(
                mode=mode,
                trial_count=len(selected),
                measured_trial_count=len(measured),
                pass_count=len(passed),
                fail_count=sum(t.verdict is TrialVerdict.FAIL for t in selected),
                blocked_count=sum(t.verdict is TrialVerdict.BLOCKED for t in selected),
                success_rate=success_rate,
                median_success_seconds=median_success,
                mean_human_interventions=mean_human,
                mean_agent_executions=mean_executions,
                backend_failures=sum(t.backend_failures for t in measured),
            )
        )
    return aggregates


def evaluate_adoption_gate(
    report: BenchmarkReport,
    manifest: BenchmarkManifest,
    policy: AdoptionGatePolicy | None = None,
) -> AdoptionGateResult:
    """Evaluate whether measured evidence is strong enough for a PCS pilot.

    Gate checks use only data already captured by the benchmark. A missing
    required comparison is never treated optimistically. By contrast, zero
    both-pass pairs after otherwise complete trials is negative evidence about
    engineering quality, not missing evidence; that therefore yields NOT_READY.
    """
    policy = policy or manifest.adoption_gate or AdoptionGatePolicy()
    checks: list[GateCheck] = []
    evidence_missing: list[str] = []

    def check(key: str, passed: bool, observed, required, detail: str) -> None:
        checks.append(
            GateCheck(
                key=key,
                passed=passed,
                observed=observed,
                required=required,
                detail=detail,
            )
        )

    complete = report.status is BenchmarkStatus.COMPLETE
    check(
        "benchmark_complete",
        complete,
        report.status.value,
        BenchmarkStatus.COMPLETE.value,
        "Blocked/missing trials invalidate the adoption decision.",
    )
    if not complete:
        evidence_missing.append("benchmark evidence is incomplete")

    task_count = len(manifest.tasks)
    check(
        "minimum_historical_tasks",
        task_count >= policy.min_tasks,
        task_count,
        f">={policy.min_tasks}",
        "The corpus must cover multiple independent historical PCS tasks.",
    )
    if task_count < policy.min_tasks:
        evidence_missing.append(
            f"only {task_count} historical task(s); need at least {policy.min_tasks}"
        )

    check(
        "minimum_repeats",
        manifest.repeats >= policy.min_repeats,
        manifest.repeats,
        f">={policy.min_repeats}",
        "Repeated paired trials reduce one-off model/run variance.",
    )
    if manifest.repeats < policy.min_repeats:
        evidence_missing.append(
            f"only {manifest.repeats} repeat(s); need at least {policy.min_repeats}"
        )

    expected_pairs = task_count * manifest.repeats
    observed_pairs = len(report.comparisons)
    incomplete_pairs = sum(item.outcome == "incomplete" for item in report.comparisons)
    pair_count_ok = not policy.require_complete_pairs or observed_pairs == expected_pairs
    pair_outcomes_ok = not policy.require_complete_pairs or incomplete_pairs == 0
    pair_ok = pair_count_ok and pair_outcomes_ok
    check(
        "complete_pairs",
        pair_ok,
        {
            "observed": observed_pairs,
            "expected": expected_pairs,
            "incomplete": incomplete_pairs,
        },
        {
            "count": expected_pairs if policy.require_complete_pairs else "not required",
            "incomplete": 0 if policy.require_complete_pairs else "not required",
        },
        "Every expected SceneWorks trial must have exactly one comparable direct-agent partner.",
    )
    if not pair_count_ok:
        evidence_missing.append(
            f"only {observed_pairs} paired comparison(s); expected {expected_pairs}"
        )
    if not pair_outcomes_ok:
        evidence_missing.append(f"{incomplete_pairs} paired comparison(s) are incomplete")

    by_mode = {aggregate.mode: aggregate for aggregate in report.aggregates}
    sw = by_mode.get("sceneworks")
    direct = by_mode.get("direct")
    if sw is None or direct is None:
        evidence_missing.append("both SceneWorks and direct aggregates are required")
        return AdoptionGateResult(
            verdict=AdoptionVerdict.INSUFFICIENT_EVIDENCE,
            checks=checks,
            blockers=evidence_missing,
            summary="PCS adoption gate cannot be evaluated without both benchmark arms.",
        )

    sw_rate = sw.success_rate
    direct_rate = direct.success_rate
    rate_present = sw_rate is not None and direct_rate is not None
    check(
        "minimum_sceneworks_success_rate",
        bool(rate_present and sw_rate >= policy.min_sceneworks_success_rate),
        sw_rate,
        f">={policy.min_sceneworks_success_rate}",
        "SceneWorks must solve a substantial majority of the historical corpus.",
    )
    if not rate_present:
        evidence_missing.append("success rates are unavailable")

    regression = (
        round(direct_rate - sw_rate, 4)
        if sw_rate is not None and direct_rate is not None
        else None
    )
    check(
        "success_rate_vs_direct",
        bool(regression is not None and regression <= policy.max_success_rate_regression),
        regression,
        f"<={policy.max_success_rate_regression} direct-minus-SceneWorks",
        "The control plane must not reduce engineering success relative to the same direct worker.",
    )

    common_ratios = [
        item.elapsed_ratio_sceneworks_over_direct
        for item in report.comparisons
        if item.elapsed_ratio_sceneworks_over_direct is not None
    ]
    median_ratio = round(statistics.median(common_ratios), 4) if common_ratios else None
    check(
        "median_elapsed_overhead",
        bool(median_ratio is not None and median_ratio <= policy.max_median_time_ratio),
        median_ratio,
        f"<={policy.max_median_time_ratio}x direct",
        (
            "Latency is compared only on pairs where both arms passed. No both-pass "
            "pairs is a failed readiness check, not evidence that should be ignored."
        ),
    )

    mean_human = sw.mean_human_interventions
    check(
        "human_intervention_overhead",
        bool(
            mean_human is not None
            and mean_human <= policy.max_mean_human_interventions
        ),
        mean_human,
        f"<={policy.max_mean_human_interventions} mean interventions/trial",
        "SceneWorks should not create excessive approval/repair burden.",
    )
    if mean_human is None:
        evidence_missing.append("human-intervention metric is unavailable")

    backend_failures = sw.backend_failures
    check(
        "backend_reliability",
        backend_failures <= policy.max_backend_failures,
        backend_failures,
        f"<={policy.max_backend_failures}",
        "Control-plane/provider failures are adoption blockers, not engineering failures.",
    )

    if evidence_missing:
        return AdoptionGateResult(
            verdict=AdoptionVerdict.INSUFFICIENT_EVIDENCE,
            checks=checks,
            blockers=evidence_missing,
            summary="PCS adoption gate lacks enough comparable evidence.",
        )

    failed = [item for item in checks if not item.passed]
    if failed:
        blockers = [f"{item.key}: {item.detail}" for item in failed]
        return AdoptionGateResult(
            verdict=AdoptionVerdict.NOT_READY,
            checks=checks,
            blockers=blockers,
            summary="Benchmark evidence is complete, but SceneWorks does not meet the configured PCS pilot thresholds.",
        )

    return AdoptionGateResult(
        verdict=AdoptionVerdict.READY_FOR_PILOT,
        checks=checks,
        blockers=[],
        summary="SceneWorks meets the configured historical benchmark thresholds and may proceed to a bounded PCS dogfood pilot.",
    )
