"""Qualification result model.

Design rules, each of which exists because the V2.5 evaluation framework broke
it (see docs/wp0-baseline-audit.md, F1):

1. **A scenario passes only if it declares checks and every check passes.**
   A scenario with no checks is BLOCKED, never PASS. The old framework computed
   ``passed = len(errors) == 0``, so "nothing was evaluated" and "everything was
   correct" produced the same verdict.

2. **Every metric is either measured or explicitly not measured.** There is no
   third state where a field holds a default that reads like a measurement.
   ``None`` means "not applicable to this scenario". A metric this harness
   cannot measure at all is named in ``UNSUPPORTED_METRICS`` and never given a
   value.

3. **A suite cannot report PASS if required scenarios did not run.** Skipped,
   blocked and not-run scenarios propagate into the suite verdict.
"""

from __future__ import annotations

import platform
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class Verdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    #: The scenario could not be evaluated — a precondition of the *harness*
    #: failed (git missing, worktree creation impossible, scenario declares no
    #: checks). Distinct from FAIL, which means SceneWorks produced a wrong
    #: engineering outcome.
    BLOCKED = "BLOCKED"
    #: Not selected by the current filter, or deliberately skipped.
    NOT_RUN = "NOT_RUN"


#: Metrics named in the WP1 roadmap that this harness genuinely cannot measure.
#: Listed so the report can say "unsupported" instead of inventing a number.
UNSUPPORTED_METRICS: dict[str, str] = {
    "architecture_usefulness": (
        "Requires human or model judgement of analysis quality. With a scripted "
        "backend the architecture text is whatever the script says, so any score "
        "would measure the script, not SceneWorks. The harness measures "
        "architecture_result_present instead, and live qualification mode "
        "records the text for human assessment."
    ),
    "requirement_quality": (
        "Same reason as architecture_usefulness: not derivable from a "
        "deterministic script."
    ),
    "cost_estimate": (
        "No token or cost accounting exists in AgentBackend. Reporting a "
        "currency figure would be fabricated."
    ),
}


@dataclass
class Check:
    """One assertion about an engineering outcome."""

    name: str
    passed: bool
    expected: Any
    actual: Any
    detail: str = ""

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "passed": self.passed,
            "expected": _jsonable(self.expected),
            "actual": _jsonable(self.actual),
            "detail": self.detail,
        }


@dataclass
class Observations:
    """What the harness actually measured while driving the workflow.

    ``None`` means "not applicable to this scenario" — never "zero" and never
    "we did not bother". Anything that cannot be measured at all is in
    UNSUPPORTED_METRICS and has no field here.
    """

    # --- routing -------------------------------------------------------
    triage_ran: bool = False
    triage_degraded: bool | None = None
    request_type: str | None = None
    requires_implementation: bool | None = None
    advisory_roles_selected: frozenset[str] | None = None
    advisory_roles_executed: frozenset[str] = frozenset()

    # --- project memory ------------------------------------------------
    #: Titles of memories the workflow injected as authoritative context. Titles
    #: rather than ids so a failing check names the decision, not a number.
    memories_injected: frozenset[str] = frozenset()
    #: Terms retrieval actually searched for — the diagnostic that was missing
    #: when whole task descriptions were used as one SQL pattern.
    memory_query_terms: tuple[str, ...] = ()
    #: Proposals that matched but were deliberately withheld from the agent.
    memories_proposed_not_injected: frozenset[str] = frozenset()

    # --- architecture --------------------------------------------------
    architecture_result_present: bool | None = None
    architecture_result_bytes: int | None = None

    # --- implementation ------------------------------------------------
    files_changed: frozenset[str] = frozenset()
    files_expected_and_changed: frozenset[str] = frozenset()
    files_expected_but_unchanged: frozenset[str] = frozenset()
    files_unexpectedly_changed: frozenset[str] = frozenset()
    base_commit: str | None = None
    result_commit: str | None = None
    diff_bytes: int | None = None
    engineer_executions: int = 0

    # --- verification --------------------------------------------------
    #: Result of running the reference repository's own check command inside the
    #: result worktree. None when the scenario produced no worktree to check.
    tests_pass_at_base: bool | None = None
    tests_pass_at_result: bool | None = None
    test_command: str | None = None
    test_output_tail: str | None = None

    # --- review --------------------------------------------------------
    review_verdicts: tuple[str, ...] = ()
    reviewer_detected_defect: bool | None = None
    #: True when the reviewer approved work whose tests do not pass.
    reviewer_false_approval: bool | None = None
    repair_iterations: int = 0

    # --- process -------------------------------------------------------
    final_task_status: str | None = None
    human_interventions: int = 0
    backend_failures: int = 0
    duration_seconds: float = 0.0
    cancellation_honoured: bool | None = None
    recovery: dict | None = None

    # --- provenance ----------------------------------------------------
    project_id: int | None = None
    task_id: int | None = None
    execution_ids: tuple[str, ...] = ()
    task_branch: str | None = None
    repository_path: str | None = None
    #: For a live provider run: what health() reported about the backend that
    #: actually did the work, so a result is attributable to a concrete
    #: version and mode rather than to "openhands".
    backend_version: str | None = None
    backend_detail: str | None = None

    def as_dict(self) -> dict:
        out = {}
        for key, value in self.__dict__.items():
            out[key] = _jsonable(value)
        return out


@dataclass
class ScenarioResult:
    key: str
    title: str
    verdict: Verdict = Verdict.NOT_RUN
    checks: list[Check] = field(default_factory=list)
    observations: Observations = field(default_factory=Observations)
    #: Harness-level problems (exceptions, timeouts). Non-empty ⇒ BLOCKED.
    blockers: list[str] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)

    def finalize(self) -> ScenarioResult:
        """Compute the verdict from checks and blockers. Never optimistic."""
        if self.blockers:
            self.verdict = Verdict.BLOCKED
        elif not self.checks:
            # Rule 1: no assertions means nothing was evaluated.
            self.blockers.append(
                "scenario declared no checks; nothing was evaluated"
            )
            self.verdict = Verdict.BLOCKED
        elif all(c.passed for c in self.checks):
            self.verdict = Verdict.PASS
        else:
            self.verdict = Verdict.FAIL
        return self

    @property
    def failed_checks(self) -> list[Check]:
        return [c for c in self.checks if not c.passed]

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "title": self.title,
            "verdict": self.verdict.value,
            "checks": [c.as_dict() for c in self.checks],
            "observations": self.observations.as_dict(),
            "blockers": list(self.blockers),
            "unsupported_metrics": list(self.unsupported),
        }


@dataclass
class QualificationReport:
    """Machine-readable release qualification result."""

    sceneworks_version: str
    backend: str
    mode: str
    started_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    finished_at: str | None = None
    duration_seconds: float = 0.0
    results: list[ScenarioResult] = field(default_factory=list)
    #: Scenario keys that qualification requires for a PASS verdict.
    required_scenarios: list[str] = field(default_factory=list)
    #: Free-text notes about the environment, not about outcomes.
    environment: dict = field(default_factory=dict)

    # ------------------------------------------------------------ verdict

    @property
    def verdict(self) -> Verdict:
        """Suite verdict.

        FAIL if any scenario failed. BLOCKED if nothing failed but a required
        scenario did not actually produce a PASS — a release must not claim PASS
        on the strength of scenarios that were skipped (WP1 requirement).
        """
        if not self.results:
            return Verdict.NOT_RUN
        by_key = {r.key: r for r in self.results}

        if any(r.verdict is Verdict.FAIL for r in self.results):
            return Verdict.FAIL

        for key in self.required_scenarios:
            result = by_key.get(key)
            if result is None or result.verdict is not Verdict.PASS:
                return Verdict.BLOCKED

        if any(r.verdict is Verdict.BLOCKED for r in self.results):
            return Verdict.BLOCKED
        if all(r.verdict is Verdict.NOT_RUN for r in self.results):
            return Verdict.NOT_RUN
        return Verdict.PASS

    @property
    def missing_required(self) -> list[str]:
        by_key = {r.key: r for r in self.results}
        return [
            key
            for key in self.required_scenarios
            if key not in by_key or by_key[key].verdict is not Verdict.PASS
        ]

    def counts(self) -> dict[str, int]:
        counts = {v.value: 0 for v in Verdict}
        for result in self.results:
            counts[result.verdict.value] += 1
        return counts

    def as_dict(self) -> dict:
        return {
            "schema": "sceneworks.qualification/1",
            "verdict": self.verdict.value,
            "sceneworks_version": self.sceneworks_version,
            "backend": self.backend,
            "mode": self.mode,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_seconds": round(self.duration_seconds, 3),
            "counts": self.counts(),
            "required_scenarios": list(self.required_scenarios),
            "missing_required": self.missing_required,
            "unsupported_metrics": UNSUPPORTED_METRICS,
            "environment": {
                "python": platform.python_version(),
                "platform": platform.platform(),
                **self.environment,
            },
            "scenarios": [r.as_dict() for r in self.results],
        }


def _jsonable(value):
    if isinstance(value, (frozenset, set)):
        return sorted(str(v) for v in value)
    if isinstance(value, tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, Enum):
        return value.value
    return value
