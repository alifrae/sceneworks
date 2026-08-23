from benchmarking.models import BenchmarkReport, TrialResult, TrialVerdict


def test_aggregates_exclude_blocked_trials_from_success_rate():
    report = BenchmarkReport(
        manifest_name="m",
        backend="fake",
        trials=[
            TrialResult(
                task_key="a", mode="sceneworks", repeat=1,
                verdict=TrialVerdict.PASS, duration_seconds=20,
                human_interventions=1, agent_executions=4,
            ),
            TrialResult(
                task_key="b", mode="sceneworks", repeat=1,
                verdict=TrialVerdict.FAIL, duration_seconds=10,
                human_interventions=1, agent_executions=3,
            ),
            TrialResult(
                task_key="c", mode="sceneworks", repeat=1,
                verdict=TrialVerdict.BLOCKED, blocker="provider unavailable",
            ),
        ],
    ).finalize(expected_trials=3)

    aggregate = report.aggregates[0]
    assert aggregate.trial_count == 3
    assert aggregate.measured_trial_count == 2
    assert aggregate.success_rate == 0.5
    assert aggregate.median_success_seconds == 20
    assert aggregate.mean_human_interventions == 1
    assert report.status.value == "INCOMPLETE"
