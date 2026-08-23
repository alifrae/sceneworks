"""WP9 productivity benchmark regression tests."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from benchmarking.cli import main
from benchmarking.models import (
    BenchmarkManifest,
    BenchmarkReport,
    BenchmarkStatus,
    BenchmarkTask,
    CommandResult,
    TrialResult,
    TrialVerdict,
)
from benchmarking.runner import _preflight
from benchmarking.scoring import compare_pair, finalize_trial
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
