from benchmarking.models import BenchmarkTask, CommandResult, TrialResult, TrialVerdict
from benchmarking.scoring import finalize_trial


def _task() -> BenchmarkTask:
    return BenchmarkTask(
        key="workflow-outcome",
        title="Workflow outcome",
        description="Prove workflow delivery is part of SceneWorks success.",
        repository_path=".",
        verification_commands=["check"],
        baseline_expectation="any",
    )


def test_sceneworks_checks_cannot_hide_failed_control_plane():
    trial = TrialResult(
        task_key="workflow-outcome",
        mode="sceneworks",
        repeat=1,
        final_task_status="FAILED",
        verification=[CommandResult(command="check", passed=True, returncode=0)],
    )

    result = finalize_trial(trial, _task())

    assert result.verdict is TrialVerdict.FAIL
    assert result.quality_gate_passed is False


def test_sceneworks_ready_for_human_can_pass_same_acceptance_gate():
    trial = TrialResult(
        task_key="workflow-outcome",
        mode="sceneworks",
        repeat=1,
        final_task_status="READY_FOR_HUMAN",
        verification=[CommandResult(command="check", passed=True, returncode=0)],
    )

    result = finalize_trial(trial, _task())

    assert result.verdict is TrialVerdict.PASS
    assert result.quality_gate_passed is True
