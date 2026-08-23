"""Outcome scoring for WP9 productivity benchmarks.

Only deterministic evidence affects the quality gate: repository verification
commands plus Git-authoritative file provenance. Subjective architecture or
review quality is intentionally not converted into a fake numeric score.
"""

from __future__ import annotations

import fnmatch
import statistics

from benchmarking.models import (
    BenchmarkTask,
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
