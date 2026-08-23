"""End-to-end plumbing test for the WP9 paired benchmark runner."""

from __future__ import annotations

import pytest

from benchmarking.models import BenchmarkManifest, BenchmarkStatus, BenchmarkTask, TrialVerdict
from benchmarking.runner import run_manifest
from tests.conftest import require_git


@pytest.mark.slow
async def test_paired_benchmark_runs_both_arms_on_same_pinned_commit(git_repo, tmp_path):
    """Exercise real worktrees, prompts, workflow/direct backends and scoring.

    The stock fake backend deliberately makes no source change, and this
    plumbing-only acceptance command does not require one. Both arms therefore
    legitimately pass. The point of this test is to prove that both execution
    paths run end to end from the same pinned commit and produce comparable
    aggregates; workflow-failure rejection is covered separately with an
    explicit FAILED lifecycle fixture.
    """
    require_git()
    manifest = BenchmarkManifest(
        name="paired-plumbing",
        backend="fake",
        repeats=1,
        tasks=[
            BenchmarkTask(
                key="plumbing",
                title="Exercise benchmark plumbing",
                description="Run both benchmark arms without requiring a source edit.",
                repository_path=git_repo,
                baseline_expectation="any",
                verification_commands=["python -c \"print('acceptance')\""],
            )
        ],
    )

    report = await run_manifest(
        manifest,
        workdir=tmp_path / "benchmark",
        modes=("sceneworks", "direct"),
    )

    assert report.status is BenchmarkStatus.COMPLETE
    assert len(report.trials) == 2
    by_mode = {trial.mode: trial for trial in report.trials}
    assert by_mode["direct"].verdict is TrialVerdict.PASS
    assert by_mode["sceneworks"].verdict is TrialVerdict.PASS
    assert by_mode["sceneworks"].final_task_status == "READY_FOR_HUMAN"
    assert by_mode["sceneworks"].resolved_base_commit == by_mode["direct"].resolved_base_commit
    assert report.comparisons[0].outcome == "both_pass"
    assert {aggregate.mode for aggregate in report.aggregates} == {"sceneworks", "direct"}
