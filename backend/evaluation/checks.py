"""Turn scenario expectations into checks against measured observations.

Each ``_check_*`` function returns zero or one Check. Returning zero means the
scenario did not state an expectation for that dimension, so nothing is
asserted — and because ``ScenarioResult.finalize()`` blocks a scenario with no
checks, an unstated scenario cannot pass by silence.

The rule throughout: never compare against a value the harness did not measure.
If an observation is ``None`` where an expectation exists, that is itself a
failure ("expected a measurement, got none"), not a silent pass.
"""

from __future__ import annotations

from evaluation.outcomes import Check, Observations
from evaluation.scenarios import Scenario


def build(scenario: Scenario, obs: Observations) -> list[Check]:
    checks: list[Check] = []
    for builder in (
        _routing,
        _requires_implementation,
        _request_type,
        _advisory_roles,
        _files_changed,
        _forbidden_files,
        _unexpected_changes,
        _result_commit,
        _tests_at_base,
        _tests_at_result,
        _reviewer_detection,
        _reviewer_false_approval,
        _repair_iterations,
        _final_status,
        _backend_failures,
        _cancellation,
        _recovery,
        _architecture_present,
        _memory_injection,
        _policy_violations,
    ):
        checks.extend(builder(scenario, obs))
    return checks


def _missing(name: str, expected) -> Check:
    return Check(
        name=name,
        passed=False,
        expected=expected,
        actual=None,
        detail="the harness produced no measurement for this dimension",
    )


# ------------------------------------------------------------------- routing


def _routing(scenario: Scenario, obs: Observations) -> list[Check]:
    """Was triage's routing correct, per the scenario's own judgement?

    Routing is correct when triage's implementation decision and request type
    match what the scenario says the request actually is. Negative controls set
    ``expect_routing_correct=False`` and pass only when the harness reports the
    routing as wrong — that is what proves the evaluator can see a bad decision.
    """
    if not obs.triage_ran:
        # Only assert routing when the scenario has something to say about it.
        if (
            scenario.expect_request_type is None
            and scenario.expect_requires_implementation is None
            and scenario.expect_routing_correct
        ):
            return []
        return [_missing("routing.triage_ran", True)]

    mismatches: list[str] = []
    if scenario.expect_requires_implementation is not None:
        if obs.requires_implementation != scenario.expect_requires_implementation:
            mismatches.append(
                f"requires_implementation={obs.requires_implementation} "
                f"(should be {scenario.expect_requires_implementation})"
            )
    if scenario.expect_request_type is not None:
        if obs.request_type != scenario.expect_request_type:
            mismatches.append(
                f"request_type={obs.request_type!r} "
                f"(should be {scenario.expect_request_type!r})"
            )

    routing_correct = not mismatches
    return [
        Check(
            name="routing.correct",
            passed=routing_correct == scenario.expect_routing_correct,
            expected=scenario.expect_routing_correct,
            actual=routing_correct,
            detail=(
                "; ".join(mismatches)
                if mismatches
                else "triage classification matched the scenario"
            ),
        )
    ]


def _requires_implementation(scenario: Scenario, obs: Observations) -> list[Check]:
    """Assert the decision itself only where routing is expected to be right.

    For a negative control the wrong decision is the point, and ``routing.correct``
    already records it; asserting it twice would fail the control for succeeding.
    """
    if scenario.expect_requires_implementation is None:
        return []
    if not scenario.expect_routing_correct:
        return []
    if obs.requires_implementation is None:
        return [_missing("routing.requires_implementation", scenario.expect_requires_implementation)]
    return [
        Check(
            name="routing.requires_implementation",
            passed=obs.requires_implementation == scenario.expect_requires_implementation,
            expected=scenario.expect_requires_implementation,
            actual=obs.requires_implementation,
        )
    ]


def _request_type(scenario: Scenario, obs: Observations) -> list[Check]:
    if scenario.expect_request_type is None or not scenario.expect_routing_correct:
        return []
    if obs.request_type is None:
        return [_missing("routing.request_type", scenario.expect_request_type)]
    return [
        Check(
            name="routing.request_type",
            passed=obs.request_type == scenario.expect_request_type,
            expected=scenario.expect_request_type,
            actual=obs.request_type,
        )
    ]


def _advisory_roles(scenario: Scenario, obs: Observations) -> list[Check]:
    if scenario.expect_advisory_roles is None:
        return []
    expected = scenario.expect_advisory_roles
    selected = obs.advisory_roles_selected
    checks = [
        Check(
            name="routing.advisory_roles_selected",
            passed=selected is not None and expected <= selected,
            expected=expected,
            actual=selected,
            detail="triage must select the advisory roles the request needs",
        ),
        # Selection without execution would be a routing claim SceneWorks did
        # not honour, so both are asserted.
        Check(
            name="routing.advisory_roles_executed",
            passed=expected <= obs.advisory_roles_executed,
            expected=expected,
            actual=obs.advisory_roles_executed,
            detail="selected advisory roles must actually have run",
        ),
    ]
    return checks


# ------------------------------------------------------------ implementation


def _files_changed(scenario: Scenario, obs: Observations) -> list[Check]:
    if not scenario.expect_files_changed:
        return []
    expected = scenario.expect_files_changed
    return [
        Check(
            name="implementation.expected_files_changed",
            passed=not obs.files_expected_but_unchanged,
            expected=expected,
            actual=obs.files_changed,
            detail=(
                "unchanged: " + ", ".join(sorted(obs.files_expected_but_unchanged))
                if obs.files_expected_but_unchanged
                else "every expected file was modified"
            ),
        )
    ]


def _forbidden_files(scenario: Scenario, obs: Observations) -> list[Check]:
    if not scenario.forbid_files_changed:
        return []
    violated = scenario.forbid_files_changed & obs.files_changed
    return [
        Check(
            name="implementation.forbidden_files_untouched",
            passed=not violated,
            expected=f"none of {sorted(scenario.forbid_files_changed)}",
            actual=sorted(violated),
            detail=(
                "these files must not change for this task"
                if violated
                else "no forbidden file was modified"
            ),
        )
    ]


def _unexpected_changes(scenario: Scenario, obs: Observations) -> list[Check]:
    """Detect changes nobody asked for.

    For an ordinary scenario the expectation is "none". The
    ``unnecessary-change`` negative control expects some, and passes only when
    the harness actually spots them.
    """
    if scenario.expect_unexpected_changes is None:
        return []
    found = bool(obs.files_unexpectedly_changed)
    return [
        Check(
            name="implementation.unnecessary_changes_detected",
            passed=found == scenario.expect_unexpected_changes,
            expected=scenario.expect_unexpected_changes,
            actual=found,
            detail=(
                "unrequested: " + ", ".join(sorted(obs.files_unexpectedly_changed))
                if found
                else "no unrequested file changed"
            ),
        )
    ]


def _result_commit(scenario: Scenario, obs: Observations) -> list[Check]:
    if scenario.expect_result_commit is None:
        return []
    # A result commit only counts when it is distinct from the base: recording
    # the base as a "result" is exactly the false-provenance case WP0 flagged.
    has_commit = bool(
        obs.result_commit and obs.result_commit != obs.base_commit
    )
    return [
        Check(
            name="provenance.result_commit",
            passed=has_commit == scenario.expect_result_commit,
            expected=scenario.expect_result_commit,
            actual=has_commit,
            detail=(
                f"base={_short(obs.base_commit)} result={_short(obs.result_commit)}"
            ),
        )
    ]


# -------------------------------------------------------------- verification


def _tests_at_base(scenario: Scenario, obs: Observations) -> list[Check]:
    if scenario.expect_tests_pass_at_base is None:
        return []
    if obs.tests_pass_at_base is None:
        return [_missing("verification.tests_at_base", scenario.expect_tests_pass_at_base)]
    return [
        Check(
            name="verification.tests_at_base",
            passed=obs.tests_pass_at_base == scenario.expect_tests_pass_at_base,
            expected=scenario.expect_tests_pass_at_base,
            actual=obs.tests_pass_at_base,
            detail=(
                "confirms the fixture really was broken before the task ran"
                if scenario.expect_tests_pass_at_base is False
                else "confirms the fixture was healthy before the task ran"
            ),
        )
    ]


def _tests_at_result(scenario: Scenario, obs: Observations) -> list[Check]:
    if scenario.expect_tests_pass_at_result is None:
        return []
    if obs.tests_pass_at_result is None:
        return [_missing("verification.tests_at_result", scenario.expect_tests_pass_at_result)]
    return [
        Check(
            name="verification.tests_at_result",
            passed=obs.tests_pass_at_result == scenario.expect_tests_pass_at_result,
            expected=scenario.expect_tests_pass_at_result,
            actual=obs.tests_pass_at_result,
            detail=(obs.test_output_tail or "")[-400:],
        )
    ]


# -------------------------------------------------------------------- review


def _reviewer_detection(scenario: Scenario, obs: Observations) -> list[Check]:
    if scenario.expect_reviewer_detects_defect is None:
        return []
    if obs.reviewer_detected_defect is None:
        return [_missing(
            "review.defect_detected", scenario.expect_reviewer_detects_defect,
        )]
    return [
        Check(
            name="review.defect_detected",
            passed=obs.reviewer_detected_defect == scenario.expect_reviewer_detects_defect,
            expected=scenario.expect_reviewer_detects_defect,
            actual=obs.reviewer_detected_defect,
            detail=f"verdicts: {list(obs.review_verdicts)}",
        )
    ]


def _reviewer_false_approval(scenario: Scenario, obs: Observations) -> list[Check]:
    if scenario.expect_reviewer_false_approval is None:
        return []
    if obs.reviewer_false_approval is None:
        return [_missing(
            "review.false_approval", scenario.expect_reviewer_false_approval,
        )]
    return [
        Check(
            name="review.false_approval",
            passed=obs.reviewer_false_approval == scenario.expect_reviewer_false_approval,
            expected=scenario.expect_reviewer_false_approval,
            actual=obs.reviewer_false_approval,
            detail=(
                "the reviewer approved work whose checks fail"
                if obs.reviewer_false_approval
                else "no approval of failing work"
            ),
        )
    ]


def _repair_iterations(scenario: Scenario, obs: Observations) -> list[Check]:
    checks: list[Check] = []
    if scenario.expect_min_repair_iterations is not None:
        checks.append(
            Check(
                name="review.repair_iterations_min",
                passed=obs.repair_iterations >= scenario.expect_min_repair_iterations,
                expected=f">= {scenario.expect_min_repair_iterations}",
                actual=obs.repair_iterations,
                detail=f"{obs.engineer_executions} engineer execution(s)",
            )
        )
    if scenario.expect_max_repair_iterations is not None:
        checks.append(
            Check(
                name="review.repair_iterations_max",
                passed=obs.repair_iterations <= scenario.expect_max_repair_iterations,
                expected=f"<= {scenario.expect_max_repair_iterations}",
                actual=obs.repair_iterations,
                detail="repairs must converge, not loop to the bound",
            )
        )
    return checks


# ------------------------------------------------------------------- process


def _final_status(scenario: Scenario, obs: Observations) -> list[Check]:
    if not scenario.expect_final_status:
        return []
    return [
        Check(
            name="process.final_status",
            passed=obs.final_task_status in scenario.expect_final_status,
            expected=scenario.expect_final_status,
            actual=obs.final_task_status,
        )
    ]


def _backend_failures(scenario: Scenario, obs: Observations) -> list[Check]:
    if scenario.expect_min_backend_failures is None:
        return []
    return [
        Check(
            name="process.backend_failures",
            passed=obs.backend_failures >= scenario.expect_min_backend_failures,
            expected=f">= {scenario.expect_min_backend_failures}",
            actual=obs.backend_failures,
            detail="a failing agent run must be recorded as a failed execution",
        )
    ]


def _cancellation(scenario: Scenario, obs: Observations) -> list[Check]:
    if scenario.expect_cancellation_honoured is None:
        return []
    if obs.cancellation_honoured is None:
        return [_missing("process.cancellation_honoured", scenario.expect_cancellation_honoured)]
    return [
        Check(
            name="process.cancellation_honoured",
            passed=obs.cancellation_honoured == scenario.expect_cancellation_honoured,
            expected=scenario.expect_cancellation_honoured,
            actual=obs.cancellation_honoured,
            detail=f"final status {obs.final_task_status}",
        )
    ]


def _recovery(scenario: Scenario, obs: Observations) -> list[Check]:
    """Recovery must be *explicit*, not merely survived.

    The requirement is that a restart surfaces what survived, what was
    interrupted, which worktree remains and what a retry will do. That is what
    is asserted here — not that an external agent process magically resumed,
    which it cannot.
    """
    if not scenario.expect_recovery_reported:
        return []
    recovery = obs.recovery
    if not recovery:
        return [_missing("recovery.reported", True)]

    required_keys = {
        "status_before_restart",
        "status_after_restart",
        "interrupted_executions",
        "completed_executions_preserved",
        "worktree_still_on_disk",
        "retry_behaviour",
    }
    missing = sorted(required_keys - set(recovery))
    checks = [
        Check(
            name="recovery.reported",
            passed=not missing,
            expected=sorted(required_keys),
            actual=sorted(recovery),
            detail=("missing: " + ", ".join(missing)) if missing else "",
        ),
        Check(
            name="recovery.interrupted_execution_recorded",
            passed=bool(recovery.get("interrupted_executions")),
            expected="at least one execution marked INTERRUPTED",
            actual=recovery.get("interrupted_executions"),
            detail=(
                "an execution that was in flight at shutdown must be recorded as "
                "interrupted rather than left looking active"
            ),
        ),
        Check(
            name="recovery.state_is_unambiguous",
            passed=_state_is_unambiguous(recovery),
            expected="a task status that does not claim work is in progress",
            actual=recovery.get("status_after_restart"),
            detail=(
                "after a restart nothing is executing and no graph is running, so "
                "a running state (ARCHITECTURE_ANALYSIS / IMPLEMENTING / REVIEWING) "
                "is a lie about the project's state. "
                + str(recovery.get("retry_behaviour") or "")
            ),
        ),
    ]
    return checks


#: States that assert an agent is currently working.
_RUNNING_STATES = {"ARCHITECTURE_ANALYSIS", "IMPLEMENTING", "REVIEWING"}


def _state_is_unambiguous(recovery: dict) -> bool:
    status = recovery.get("status_after_restart")
    if status is None:
        return False
    # An execution was interrupted, so whatever was running is gone. Remaining
    # in a running state leaves the operator unable to tell whether to wait or
    # to retry.
    return status not in _RUNNING_STATES


def _architecture_present(scenario: Scenario, obs: Observations) -> list[Check]:
    if scenario.expect_architecture_present is None:
        return []
    if obs.architecture_result_present is None:
        return [_missing("architecture.present", scenario.expect_architecture_present)]
    return [
        Check(
            name="architecture.present",
            passed=obs.architecture_result_present == scenario.expect_architecture_present,
            expected=scenario.expect_architecture_present,
            actual=obs.architecture_result_present,
            detail=(
                f"{obs.architecture_result_bytes} bytes of analysis recorded. "
                "Usefulness is not scored — see unsupported_metrics."
            ),
        )
    ]


def _memory_injection(scenario: Scenario, obs: Observations) -> list[Check]:
    """Accepted decisions must be injected; proposals must not be.

    Two separate checks on purpose. "The right thing was injected" and "the wrong
    thing was withheld" fail for different reasons: the first is the retrieval bug
    WP2 fixed, the second is the invariant that speculation never becomes
    authoritative project truth.
    """
    checks: list[Check] = []

    if scenario.expect_memories_injected:
        missing = scenario.expect_memories_injected - obs.memories_injected
        checks.append(
            Check(
                name="memory.relevant_accepted_injected",
                passed=not missing,
                expected=scenario.expect_memories_injected,
                actual=obs.memories_injected,
                detail=(
                    "not injected: " + "; ".join(sorted(missing))
                    + f" (retrieval searched for: {list(obs.memory_query_terms)})"
                    if missing
                    else f"retrieval searched for {list(obs.memory_query_terms)}"
                ),
            )
        )

    if scenario.forbid_memories_injected:
        leaked = scenario.forbid_memories_injected & obs.memories_injected
        checks.append(
            Check(
                name="memory.speculation_not_injected",
                passed=not leaked,
                expected=f"none of {sorted(scenario.forbid_memories_injected)}",
                actual=sorted(leaked),
                detail=(
                    "a proposal or irrelevant memory reached the authoritative "
                    "context block"
                    if leaked
                    else "only accepted, relevant memories were injected"
                ),
            )
        )

    return checks


def _policy_violations(scenario: Scenario, obs: Observations) -> list[Check]:
    """WP4 closure evidence: a violation must be found by SceneWorks itself.

    Checked against the policy.violation_detected event, not against the
    Reviewer's verdict text -- a scripted backend's approval or rejection
    proves nothing about whether SceneWorks actually caught the violation. The
    scenario's Reviewer script can (and does, in policy-violation) approve
    anyway, exactly like the intentional-regression negative control: the
    point is that the deterministic mechanism does not depend on the model
    noticing.
    """
    if not scenario.expect_policy_violations:
        return []
    missing = scenario.expect_policy_violations - obs.policy_violations_detected
    return [
        Check(
            name="policy.violation_detected",
            passed=not missing,
            expected=scenario.expect_policy_violations,
            actual=obs.policy_violations_detected,
            detail=(
                "not detected: " + ", ".join(sorted(missing))
                if missing
                else "SceneWorks' deterministic protected-path check found "
                "every expected violation, independent of the review verdict"
            ),
        )
    ]


def _short(commit: str | None) -> str:
    return commit[:8] if commit else "-"
