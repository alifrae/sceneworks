"""Productivity benchmark and WP12 adoption-gate regression tests."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from benchmarking.cli import load_manifest, main
from benchmarking.models import (
    AdoptionGatePolicy,
    AdoptionVerdict,
    BenchmarkManifest,
    BenchmarkReport,
    BenchmarkStatus,
    BenchmarkTask,
    CommandResult,
    ModeAggregate,
    PairComparison,
    TrialResult,
    TrialVerdict,
)
from benchmarking.runner import _preflight
from benchmarking.scoring import compare_pair, evaluate_adoption_gate, finalize_trial
from tests.conftest import require_git


def _task(**overrides) -> BenchmarkTask:
    data = {
        "key": "bug-fix",
        "title": "Fix regression",
        "description": "Repair the seeded regression.",
        "repository_path": ".",
        "verification_commands": ["python check.py"],
        "expected_changed_files": ["src/*.py"],
        "forbidden_changed_files": ["tests/**"],
    }
    data.update(overrides)
    return BenchmarkTask.model_validate(data)


def test_manifest_requires_real_verification_commands():
    with pytest.raises(ValidationError):
        _task(verification_commands=[])


def test_manifest_rejects_duplicate_task_keys():
    task = _task()
    with pytest.raises(ValidationError, match="unique"):
        BenchmarkManifest(name="dup", tasks=[task, task])


def test_trial_pass_requires_commands_and_file_constraints():
    trial = TrialResult(
        task_key="bug-fix",
        mode="direct",
        repeat=1,
        files_changed=["src/core.py"],
        verification=[CommandResult(command="check", passed=True, returncode=0)],
    )
    result = finalize_trial(trial, _task())
    assert result.verdict is TrialVerdict.PASS
    assert result.quality_gate_passed is True


def test_forbidden_change_turns_engineering_outcome_into_fail():
    trial = TrialResult(
        task_key="bug-fix",
        mode="sceneworks",
        repeat=1,
        files_changed=["src/core.py", "tests/test_core.py"],
        verification=[CommandResult(command="check", passed=True, returncode=0)],
    )
    result = finalize_trial(trial, _task())
    assert result.verdict is TrialVerdict.FAIL
    assert result.forbidden_files_changed == ["tests/test_core.py"]


def test_failed_engineering_trial_is_valid_complete_benchmark_evidence():
    failed = TrialResult(
        task_key="x", mode="direct", repeat=1, verdict=TrialVerdict.FAIL
    )
    report = BenchmarkReport(manifest_name="m", backend="fake", trials=[failed])
    report.finalize(expected_trials=1)
    assert report.status is BenchmarkStatus.COMPLETE


def test_blocked_trial_makes_benchmark_incomplete():
    blocked = TrialResult(
        task_key="x", mode="direct", repeat=1, verdict=TrialVerdict.BLOCKED,
        blocker="provider unavailable",
    )
    report = BenchmarkReport(manifest_name="m", backend="fake", trials=[blocked])
    report.finalize(expected_trials=1)
    assert report.status is BenchmarkStatus.INCOMPLETE


def test_comparison_never_uses_speed_to_hide_quality_failure():
    sw = TrialResult(
        task_key="x", mode="sceneworks", repeat=1,
        verdict=TrialVerdict.FAIL, duration_seconds=1,
    )
    direct = TrialResult(
        task_key="x", mode="direct", repeat=1,
        verdict=TrialVerdict.PASS, duration_seconds=100,
    )
    comparison = compare_pair(sw, direct)
    assert comparison.outcome == "direct_only_pass"
    assert comparison.elapsed_ratio_sceneworks_over_direct is None


def test_speed_ratio_is_only_reported_when_both_pass():
    sw = TrialResult(
        task_key="x", mode="sceneworks", repeat=1,
        verdict=TrialVerdict.PASS, duration_seconds=20,
    )
    direct = TrialResult(
        task_key="x", mode="direct", repeat=1,
        verdict=TrialVerdict.PASS, duration_seconds=10,
    )
    comparison = compare_pair(sw, direct)
    assert comparison.outcome == "both_pass"
    assert comparison.elapsed_ratio_sceneworks_over_direct == 2.0


def test_cli_validate_does_not_run_agents(tmp_path, capsys):
    manifest = {
        "schema_version": 1,
        "name": "schema-only",
        "backend": "fake",
        "tasks": [
            {
                "key": "one",
                "title": "One",
                "description": "One task",
                "repository_path": ".",
                "verification_commands": ["python check.py"],
            }
        ],
    }
    path = tmp_path / "bench.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    assert main(["--manifest", str(path), "--validate"]) == 0
    assert "valid benchmark manifest" in capsys.readouterr().out


def test_repository_path_supports_environment_substitution(tmp_path, monkeypatch):
    monkeypatch.setenv("PCS_REPO", str(tmp_path / "pcs"))
    payload = {
        "schema_version": 1,
        "name": "portable-pcs",
        "tasks": [
            {
                "key": "one",
                "title": "One",
                "description": "One historical PCS task",
                "repository_path": "${PCS_REPO}",
                "verification_commands": ["python check.py"],
            }
        ],
    }
    path = tmp_path / "pcs.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    manifest = load_manifest(path)
    assert manifest.tasks[0].repository_path == tmp_path / "pcs"


def test_disabled_manifest_gate_is_not_announced_during_validation(tmp_path, capsys):
    payload = {
        "schema_version": 1,
        "name": "disabled-gate",
        "adoption_gate": {"enabled": False},
        "tasks": [
            {
                "key": "one",
                "title": "One",
                "description": "One task",
                "repository_path": ".",
                "verification_commands": ["python check.py"],
            }
        ],
    }
    path = tmp_path / "bench.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert main(["--manifest", str(path), "--validate"]) == 0
    assert "with adoption gate" not in capsys.readouterr().out


def _gate_manifest(task_count=5, repeats=3, **policy_overrides):
    policy = AdoptionGatePolicy(**policy_overrides)
    return BenchmarkManifest(
        name="PCS gate",
        repeats=repeats,
        adoption_gate=policy,
        tasks=[
            _task(key=f"task-{index}", expected_changed_files=[], forbidden_changed_files=[])
            for index in range(task_count)
        ],
    )


def _pair_matrix(*, task_count=5, repeats=3, outcome="both_pass", ratio=1.25):
    return [
        PairComparison(
            task_key=f"task-{task_index}",
            repeat=repeat,
            outcome=outcome,
            sceneworks_seconds=12.5,
            direct_seconds=10.0,
            elapsed_ratio_sceneworks_over_direct=(
                ratio if outcome == "both_pass" else None
            ),
        )
        for task_index in range(task_count)
        for repeat in range(1, repeats + 1)
    ]


def _ready_report(
    *,
    sw_rate=1.0,
    direct_rate=1.0,
    ratio=1.25,
    sw_failures=0,
    comparisons=None,
):
    comparisons = comparisons or _pair_matrix(ratio=ratio)
    return BenchmarkReport(
        manifest_name="PCS gate",
        backend="fake",
        status=BenchmarkStatus.COMPLETE,
        comparisons=comparisons,
        aggregates=[
            ModeAggregate(
                mode="sceneworks",
                trial_count=15,
                measured_trial_count=15,
                pass_count=round(15 * sw_rate),
                fail_count=15 - round(15 * sw_rate),
                blocked_count=0,
                success_rate=sw_rate,
                median_success_seconds=12.5 if sw_rate else None,
                mean_human_interventions=1.0,
                mean_agent_executions=3.0,
                backend_failures=sw_failures,
            ),
            ModeAggregate(
                mode="direct",
                trial_count=15,
                measured_trial_count=15,
                pass_count=round(15 * direct_rate),
                fail_count=15 - round(15 * direct_rate),
                blocked_count=0,
                success_rate=direct_rate,
                median_success_seconds=10.0 if direct_rate else None,
                mean_human_interventions=0.0,
                mean_agent_executions=1.0,
                backend_failures=0,
            ),
        ],
    )


def test_adoption_gate_ready_requires_quality_and_bounded_overhead():
    manifest = _gate_manifest()
    result = evaluate_adoption_gate(_ready_report(), manifest)
    assert result.verdict is AdoptionVerdict.READY_FOR_PILOT
    assert result.blockers == []
    assert all(check.passed for check in result.checks)


def test_adoption_gate_complete_benchmark_can_still_be_not_ready():
    manifest = _gate_manifest()
    report = _ready_report(sw_rate=0.8, direct_rate=1.0)
    result = evaluate_adoption_gate(report, manifest)
    assert result.verdict is AdoptionVerdict.NOT_READY
    failed = {check.key for check in result.checks if not check.passed}
    assert "success_rate_vs_direct" in failed


def test_adoption_gate_incomplete_corpus_is_insufficient_evidence():
    manifest = _gate_manifest(task_count=2, repeats=1)
    comparisons = _pair_matrix(task_count=2, repeats=1)
    result = evaluate_adoption_gate(
        _ready_report(comparisons=comparisons), manifest
    )
    assert result.verdict is AdoptionVerdict.INSUFFICIENT_EVIDENCE
    assert any("historical task" in blocker for blocker in result.blockers)
    assert any("repeat" in blocker for blocker in result.blockers)


def test_adoption_gate_missing_expected_pairs_is_insufficient_evidence():
    manifest = _gate_manifest()
    comparisons = _pair_matrix()[:-1]
    result = evaluate_adoption_gate(
        _ready_report(comparisons=comparisons), manifest
    )
    assert result.verdict is AdoptionVerdict.INSUFFICIENT_EVIDENCE
    assert any("paired comparison" in blocker for blocker in result.blockers)


def test_adoption_gate_zero_both_pass_pairs_is_not_ready_not_missing_evidence():
    manifest = _gate_manifest()
    comparisons = _pair_matrix(outcome="direct_only_pass")
    result = evaluate_adoption_gate(
        _ready_report(sw_rate=0.0, direct_rate=1.0, comparisons=comparisons),
        manifest,
    )
    assert result.verdict is AdoptionVerdict.NOT_READY
    failed = {check.key for check in result.checks if not check.passed}
    assert "minimum_sceneworks_success_rate" in failed
    assert "success_rate_vs_direct" in failed
    assert "median_elapsed_overhead" in failed


def test_adoption_gate_backend_failure_blocks_readiness():
    manifest = _gate_manifest()
    result = evaluate_adoption_gate(_ready_report(sw_failures=1), manifest)
    assert result.verdict is AdoptionVerdict.NOT_READY
    failed = {check.key for check in result.checks if not check.passed}
    assert "backend_reliability" in failed


async def test_preflight_proves_must_fail_baseline(git_repo, tmp_path):
    require_git()
    task = _task(
        repository_path=git_repo,
        verification_commands=["python -c \"import sys; sys.exit(1)\""],
        expected_changed_files=[],
        forbidden_changed_files=[],
        baseline_expectation="must_fail",
    )
    base, blocker, commands = await _preflight(task, "fake", tmp_path / "preflight")
    assert base
    assert blocker is None
    assert commands and commands[0].passed is False


async def test_preflight_blocks_task_that_is_already_solved(git_repo, tmp_path):
    require_git()
    task = _task(
        repository_path=git_repo,
        verification_commands=["python -c \"print('already passes')\""],
        expected_changed_files=[],
        forbidden_changed_files=[],
        baseline_expectation="must_fail",
    )
    base, blocker, commands = await _preflight(task, "fake", tmp_path / "preflight")
    assert base
    assert commands and commands[0].passed is True
    assert "already pass" in (blocker or "")
