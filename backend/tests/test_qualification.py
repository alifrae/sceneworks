"""Tests for the qualification suite itself (WP1).

The suite's whole value is that a wrong engineering outcome makes it fail. The
V2.5 framework it replaced reported 8/8 PASS in 5.1 s while printing
"implementation: INCORRECT" beside every one, because ``passed`` was computed as
"no exception was raised" (docs/wp0-baseline-audit.md, F1).

So these tests do not check that the suite passes. They check that it **fails
when it should**:

- a scenario that asserts nothing is BLOCKED, never PASS;
- a mismatch between expectation and measurement produces a failing check;
- a partial run cannot report PASS for a release;
- and — the one that matters — mutating a scenario so SceneWorks genuinely
  produces the wrong outcome turns a PASS into a FAIL end to end.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from app.agents.fake import ScriptStep, triage_summary

from evaluation.checks import build as build_checks
from evaluation.harness import run_scenario
from evaluation.outcomes import (
    Check,
    Observations,
    QualificationReport,
    ScenarioResult,
    UNSUPPORTED_METRICS,
    Verdict,
)
from evaluation.refrepo import (
    CALC_BUGGY,
    CALC_HEALTHY,
    materialize,
    run_check,
)
from evaluation.scenarios import (
    REQUIRED_KEYS,
    SCENARIOS,
    SCENARIOS_BY_KEY,
    SMOKE_KEYS,
    select,
)

from tests.conftest import require_git

# ------------------------------------------------------- verdict arithmetic


def test_scenario_with_no_checks_is_blocked_not_passed():
    """The F1 regression guard: nothing evaluated must never read as success."""
    result = ScenarioResult(key="empty", title="asserts nothing").finalize()

    assert result.verdict is Verdict.BLOCKED
    assert any("no checks" in b for b in result.blockers)


def test_scenario_with_all_checks_passing_is_pass():
    result = ScenarioResult(key="k", title="t")
    result.checks = [Check("a", True, 1, 1), Check("b", True, 2, 2)]
    assert result.finalize().verdict is Verdict.PASS


def test_scenario_with_one_failing_check_is_fail():
    result = ScenarioResult(key="k", title="t")
    result.checks = [Check("a", True, 1, 1), Check("b", False, 2, 3)]
    assert result.finalize().verdict is Verdict.FAIL


def test_blocker_beats_passing_checks():
    """A harness problem must not be laundered into a PASS by other checks."""
    result = ScenarioResult(key="k", title="t")
    result.checks = [Check("a", True, 1, 1)]
    result.blockers.append("git unavailable")
    assert result.finalize().verdict is Verdict.BLOCKED


def _report(results, required):
    return QualificationReport(
        sceneworks_version="test",
        backend="fake",
        mode="test",
        results=results,
        required_scenarios=required,
    )


def _passing(key):
    result = ScenarioResult(key=key, title=key)
    result.checks = [Check("c", True, 1, 1)]
    return result.finalize()


def _failing(key):
    result = ScenarioResult(key=key, title=key)
    result.checks = [Check("c", False, 1, 2)]
    return result.finalize()


def test_suite_is_blocked_when_a_required_scenario_was_not_run():
    """A release must not claim PASS on the strength of scenarios it skipped."""
    report = _report([_passing("a")], required=["a", "b"])

    assert report.verdict is Verdict.BLOCKED
    assert report.missing_required == ["b"]


def test_suite_passes_only_when_every_required_scenario_passed():
    report = _report([_passing("a"), _passing("b")], required=["a", "b"])
    assert report.verdict is Verdict.PASS
    assert report.missing_required == []


def test_suite_fails_when_any_scenario_fails():
    report = _report([_passing("a"), _failing("b")], required=["a", "b"])
    assert report.verdict is Verdict.FAIL


def test_suite_fail_outranks_blocked():
    """A failure must not be downgraded to BLOCKED by an unrelated blocker."""
    blocked = ScenarioResult(key="c", title="c").finalize()
    report = _report([_failing("a"), blocked], required=[])
    assert report.verdict is Verdict.FAIL


def test_empty_suite_is_not_run():
    assert _report([], required=[]).verdict is Verdict.NOT_RUN


# ------------------------------------------------------------- check building


def _obs(**kwargs) -> Observations:
    return Observations(**kwargs)


def test_expectation_without_measurement_fails_rather_than_passing():
    """A missing measurement is a failure, not a silent pass."""
    scenario = dataclasses.replace(
        SCENARIOS_BY_KEY["bug-fix"], expect_tests_pass_at_result=True,
    )
    checks = build_checks(scenario, _obs(triage_ran=True, tests_pass_at_result=None))

    by_name = {c.name: c for c in checks}
    assert by_name["verification.tests_at_result"].passed is False
    assert "no measurement" in by_name["verification.tests_at_result"].detail


def test_wrong_files_changed_produces_a_failing_check():
    scenario = SCENARIOS_BY_KEY["bug-fix"]
    obs = _obs(
        triage_ran=True,
        requires_implementation=True,
        files_changed=frozenset({"unrelated.py"}),
        files_expected_but_unchanged=frozenset({"calc/core.py"}),
    )
    by_name = {c.name: c for c in build_checks(scenario, obs)}

    assert by_name["implementation.expected_files_changed"].passed is False


def test_touching_a_forbidden_file_produces_a_failing_check():
    """The bug-fix scenario forbids editing the repository's own check suite."""
    scenario = SCENARIOS_BY_KEY["bug-fix"]
    obs = _obs(
        triage_ran=True,
        requires_implementation=True,
        files_changed=frozenset({"calc/core.py", "check.py"}),
    )
    by_name = {c.name: c for c in build_checks(scenario, obs)}

    assert by_name["implementation.forbidden_files_untouched"].passed is False
    assert "check.py" in str(by_name["implementation.forbidden_files_untouched"].actual)


def test_base_commit_reported_as_result_commit_is_not_a_result():
    """Announcing the base commit as a result is false provenance."""
    scenario = SCENARIOS_BY_KEY["bug-fix"]
    obs = _obs(
        triage_ran=True,
        requires_implementation=True,
        base_commit="abc123",
        result_commit="abc123",
    )
    by_name = {c.name: c for c in build_checks(scenario, obs)}

    assert by_name["provenance.result_commit"].passed is False


def test_reviewer_approving_failing_work_is_a_false_approval():
    scenario = SCENARIOS_BY_KEY["bug-fix"]
    obs = _obs(
        triage_ran=True,
        requires_implementation=True,
        review_verdicts=("APPROVED",),
        tests_pass_at_result=False,
        reviewer_false_approval=True,
    )
    by_name = {c.name: c for c in build_checks(scenario, obs)}

    # bug-fix expects no false approval, so measuring one must fail.
    assert by_name["review.false_approval"].passed is False


def test_recovery_leaving_a_running_state_is_ambiguous():
    """A task claiming to be running after a restart is a false claim."""
    scenario = SCENARIOS_BY_KEY["restart-recovery"]
    recovery = {
        "status_before_restart": "ARCHITECTURE_ANALYSIS",
        "status_after_restart": "ARCHITECTURE_ANALYSIS",
        "interrupted_executions": ["x"],
        "completed_executions_preserved": [],
        "worktree_still_on_disk": False,
        "retry_behaviour": "-",
    }
    by_name = {
        c.name: c
        for c in build_checks(scenario, _obs(triage_ran=True, recovery=recovery))
    }

    assert by_name["recovery.state_is_unambiguous"].passed is False


def test_negative_control_fails_if_the_bad_outcome_stops_being_detected():
    """If the harness stopped seeing the seeded regression, the control fails.

    This is what keeps the negative controls honest: they must not pass simply
    because nothing was measured.
    """
    scenario = SCENARIOS_BY_KEY["intentional-regression"]
    # Pretend the regression did not happen — checks pass at the result.
    obs = _obs(
        triage_ran=True,
        requires_implementation=True,
        tests_pass_at_base=True,
        tests_pass_at_result=True,
        review_verdicts=("APPROVED",),
        reviewer_false_approval=False,
        files_changed=frozenset({"calc/core.py"}),
        base_commit="a",
        result_commit="b",
    )
    failed = {c.name for c in build_checks(scenario, obs) if not c.passed}

    assert "verification.tests_at_result" in failed
    assert "review.false_approval" in failed


# --------------------------------------------------------- honest reporting


def test_unsupported_metrics_are_named_with_reasons_not_scored():
    assert "architecture_usefulness" in UNSUPPORTED_METRICS
    assert "cost_estimate" in UNSUPPORTED_METRICS
    for name, reason in UNSUPPORTED_METRICS.items():
        assert len(reason) > 40, f"{name} needs a real explanation, not a label"

    # And no Observations field silently provides one of them anyway.
    fields = {f.name for f in dataclasses.fields(Observations)}
    assert fields.isdisjoint(UNSUPPORTED_METRICS)


def test_report_json_is_serialisable_and_self_describing():
    report = _report([_passing("a")], required=["a"])
    payload = json.loads(json.dumps(report.as_dict()))

    assert payload["schema"] == "sceneworks.qualification/1"
    assert payload["verdict"] == "PASS"
    assert payload["counts"]["PASS"] == 1
    assert "unsupported_metrics" in payload
    assert payload["scenarios"][0]["key"] == "a"


def test_exit_codes_cover_every_verdict():
    from evaluation.cli import EXIT_CODES

    assert set(EXIT_CODES) == set(Verdict)
    # PASS must be the only zero: CI treats non-zero as "do not release".
    assert EXIT_CODES[Verdict.PASS] == 0
    assert all(code != 0 for v, code in EXIT_CODES.items() if v is not Verdict.PASS)


# ------------------------------------------------------------ scenario registry


def test_every_required_scenario_class_from_wp1_is_present():
    """WP1 enumerates the scenario classes qualification must support."""
    required_classes = {
        "architecture-investigation",
        "bug-fix",
        "small-feature",
        "multi-file-feature",
        "refactoring",
        "api-modification",
        "intentional-regression",
        "reviewer-detects-defect",
        "reviewer-engineer-repair-loop",
        "ambiguous-requirement",
        "no-implementation-needed",
        "documentation-only",
        "performance-task",
        "failed-execution",
        "cancellation",
        "restart-recovery",
        "unnecessary-change",
        "incorrect-triage",
        # Added by WP2: proves accepted decisions reach the agent and proposals
        # do not, on the real workflow path.
        "memory-injection",
    }
    assert required_classes <= set(SCENARIOS_BY_KEY)


def test_no_scenario_is_defined_without_expectations():
    """A scenario that asserts nothing would be BLOCKED at runtime; catch it here."""
    asserting_fields = [
        "expect_request_type",
        "expect_requires_implementation",
        "expect_advisory_roles",
        "expect_files_changed",
        "forbid_files_changed",
        "expect_result_commit",
        "expect_unexpected_changes",
        "expect_tests_pass_at_base",
        "expect_tests_pass_at_result",
        "expect_reviewer_detects_defect",
        "expect_reviewer_false_approval",
        "expect_min_repair_iterations",
        "expect_final_status",
        "expect_min_backend_failures",
        "expect_cancellation_honoured",
        "expect_recovery_reported",
        "expect_architecture_present",
        "expect_memories_injected",
        "forbid_memories_injected",
    ]
    for scenario in SCENARIOS:
        stated = [
            name
            for name in asserting_fields
            if getattr(scenario, name) not in (None, frozenset(), False)
        ]
        # A negative control states its expectation via expect_routing_correct.
        if not scenario.expect_routing_correct:
            stated.append("expect_routing_correct")
        assert stated, f"scenario {scenario.key} asserts nothing"


def test_smoke_subset_is_a_real_subset_and_covers_a_negative_control():
    assert set(SMOKE_KEYS) <= set(SCENARIOS_BY_KEY)
    assert "intentional-regression" in SMOKE_KEYS, (
        "the smoke subset must include a negative control, otherwise a broken "
        "harness passes CI"
    )


def test_select_rejects_unknown_scenarios():
    with pytest.raises(KeyError):
        select(["no-such-scenario"])


def test_required_keys_are_all_real():
    assert set(REQUIRED_KEYS) <= set(SCENARIOS_BY_KEY)


# ------------------------------------------------------------ reference repos


def test_reference_repos_behave_as_they_declare(tmp_path):
    """A fixture that lies makes every outcome measurement meaningless."""
    require_git()
    for repo in (CALC_BUGGY, CALC_HEALTHY):
        root, base = materialize(repo, tmp_path / repo.name)
        assert base
        passed, output = run_check(root)
        assert passed is not repo.broken_at_base, (
            f"{repo.name} declares broken_at_base={repo.broken_at_base} but its "
            f"checks {'passed' if passed else 'failed'}: {output}"
        )


def test_reference_repo_fixture_lie_is_caught_by_the_harness(tmp_path):
    """Mislabel a fixture and the harness must BLOCK, not measure nonsense."""
    require_git()
    scenario = dataclasses.replace(
        SCENARIOS_BY_KEY["bug-fix"],
        key="mislabelled",
        # calc-healthy passes at base, but bug-fix's repo is declared broken.
        repo=CALC_HEALTHY.name,
        expect_tests_pass_at_base=False,
    )
    # The scenario now claims the base is broken while pointing at a healthy
    # repository; the resulting check must fail rather than quietly pass.
    obs = _obs(triage_ran=True, requires_implementation=True, tests_pass_at_base=True)
    by_name = {c.name: c for c in build_checks(scenario, obs)}
    assert by_name["verification.tests_at_base"].passed is False


# ---------------------------------------------------- end-to-end mutation test


@pytest.mark.slow
async def test_bug_fix_scenario_passes_end_to_end(tmp_path):
    """Baseline for the mutation test below: the unmodified scenario passes."""
    require_git()
    result = await run_scenario(SCENARIOS_BY_KEY["bug-fix"], tmp_path, timeout=120)

    assert result.verdict is Verdict.PASS, (
        f"blockers={result.blockers} "
        f"failed={[(c.name, c.actual) for c in result.failed_checks]}"
    )
    obs = result.observations
    # The measurements that make this a real outcome evaluation.
    assert obs.tests_pass_at_base is False
    assert obs.tests_pass_at_result is True
    assert obs.result_commit and obs.result_commit != obs.base_commit
    assert "calc/core.py" in obs.files_changed


@pytest.mark.slow
async def test_engineer_that_changes_nothing_makes_the_suite_fail(tmp_path):
    """THE WP1 closure test.

    Same scenario, same expectations — but the Engineer produces no code. A
    framework that only catches exceptions would still report PASS here, because
    nothing raises: the workflow runs, the reviewer approves, the task reaches
    READY_FOR_HUMAN. Qualification must report FAIL because the *engineering
    outcome* is wrong.
    """
    require_git()
    broken = dataclasses.replace(
        SCENARIOS_BY_KEY["bug-fix"],
        key="bug-fix-mutated",
        role_scripts={
            **SCENARIOS_BY_KEY["bug-fix"].role_scripts,
            "engineer": [
                ScriptStep(kind="summary", summary="Looks fine to me; nothing to do."),
            ],
        },
    )
    result = await run_scenario(broken, tmp_path, timeout=120)

    assert result.verdict is Verdict.FAIL, (
        "an Engineer that fixed nothing must fail qualification; got "
        f"{result.verdict} with blockers={result.blockers}"
    )
    failed = {c.name for c in result.failed_checks}
    # The bug was never fixed, so the repository's own checks still fail...
    assert "verification.tests_at_result" in failed
    # ...and no commit was produced.
    assert "provenance.result_commit" in failed


@pytest.mark.slow
async def test_reviewer_approving_a_regression_is_reported_as_false_approval(tmp_path):
    """A reviewer that approves broken work must be measured, not trusted."""
    require_git()
    result = await run_scenario(
        SCENARIOS_BY_KEY["intentional-regression"], tmp_path, timeout=120,
    )

    assert result.verdict is Verdict.PASS, (
        f"the negative control must detect the regression; blockers={result.blockers} "
        f"failed={[c.name for c in result.failed_checks]}"
    )
    obs = result.observations
    assert obs.tests_pass_at_base is True
    assert obs.tests_pass_at_result is False
    assert obs.reviewer_false_approval is True


@pytest.mark.slow
async def test_memory_injection_scenario_fails_when_the_decision_is_unaccepted(tmp_path):
    """Ties WP1 and WP2 together end to end.

    The `memory-injection` scenario passes because an accepted decision reaches
    the agent. Downgrade that same decision to `proposed` and the scenario must
    fail — proving the check observes real injection rather than just the presence
    of a memory row, and that a proposal cannot become authoritative context.
    """
    require_git()
    scenario = SCENARIOS_BY_KEY["memory-injection"]
    downgraded = dataclasses.replace(
        scenario,
        key="memory-injection-mutated",
        seed_memories=tuple(
            {**spec, "status": "proposed"}
            if spec["title"] == "Aggregation goes through calc.core"
            else spec
            for spec in scenario.seed_memories
        ),
    )
    result = await run_scenario(downgraded, tmp_path, timeout=120)

    assert result.verdict is Verdict.FAIL, (
        "an unaccepted decision must not satisfy the injection check; got "
        f"{result.verdict} with blockers={result.blockers}"
    )
    assert "memory.relevant_accepted_injected" in {
        c.name for c in result.failed_checks
    }
    assert "Aggregation goes through calc.core" not in result.observations.memories_injected


@pytest.mark.slow
async def test_wrong_triage_routing_is_detected(tmp_path):
    """Routing correctness is measured, which the fake-backend bypass prevented.

    Before WP1 the workflow skipped the Triage node entirely whenever the backend
    was `fake` (WP0 finding F3), so no automated test could observe a routing
    decision at all.
    """
    require_git()
    scenario = dataclasses.replace(
        SCENARIOS_BY_KEY["no-implementation-needed"],
        key="routing-mutated",
        role_scripts={
            **SCENARIOS_BY_KEY["no-implementation-needed"].role_scripts,
            # Triage now wrongly claims code must be written.
            "triage": [ScriptStep(kind="summary", summary=triage_summary(
                request_type="feature",
                requires_implementation=True,
            ))],
        },
    )
    result = await run_scenario(scenario, tmp_path, timeout=120)

    assert result.verdict is Verdict.FAIL, (
        f"wrong routing must fail; got {result.verdict}, blockers={result.blockers}"
    )
    failed = {c.name for c in result.failed_checks}
    assert "routing.correct" in failed or "routing.requires_implementation" in failed
    assert result.observations.requires_implementation is True
