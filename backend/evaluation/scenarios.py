"""Qualification scenarios.

A scenario declares two things:

- **how to drive SceneWorks** — the reference repository, the scripted agent
  behaviour per role, and which driver the harness should use;
- **what a correct engineering outcome looks like** — expectations the harness
  turns into checks against what it actually measured.

Expectations left as ``None`` are not asserted. A scenario that asserts nothing
is BLOCKED by ``ScenarioResult.finalize()``, so an under-specified scenario can
never contribute a PASS.

Three scenarios are deliberate **negative controls**: they seed a wrong
engineering outcome and pass only when the harness *detects* it. They are the
scenarios that prove the suite can fail for a bad outcome rather than only for a
Python exception — the WP1 closure criterion.

  - ``intentional-regression``      Engineer breaks a healthy repository.
  - ``unnecessary-change``          Engineer edits files nobody asked for.
  - ``incorrect-triage``            Triage routes a pure question to code.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.agents.fake import ScriptStep, triage_summary

from evaluation.refrepo import (
    CALC_BUGGY,
    CALC_CORE_FIXED,
    CALC_CORE_REGRESSED,
    CALC_HEALTHY,
)

# --------------------------------------------------------------------- drivers

#: Approve the architecture, let the Engineer and Reviewer run to a terminal state.
DRIVE_FULL = "full"
#: No implementation expected; the workflow should finish after the Architect.
DRIVE_ADVISORY = "advisory"
#: Cancel the task while an agent is running.
DRIVE_CANCEL = "cancel"
#: Tear the process down mid-workflow and rebuild it against the same database.
DRIVE_RESTART = "restart"


# ------------------------------------------------------------- script helpers


def _arch(summary: str = "Architecture analysis: proceed as described.") -> list[ScriptStep]:
    return [ScriptStep(kind="summary", summary=summary)]


def _write(path: str, content: str) -> ScriptStep:
    return ScriptStep(kind="file", path=path, content=content)


def _commit(message: str = "implement task") -> ScriptStep:
    return ScriptStep(kind="commit", message=message)


def _review(verdict: str, note: str = "") -> list[ScriptStep]:
    body = note or ("Implementation matches the approved architecture."
                    if verdict == "APPROVED" else
                    "Implementation does not satisfy the requirement.")
    return [ScriptStep(kind="summary", summary=f"{body}\nVERDICT: {verdict}")]


def _advisory(role: str) -> list[ScriptStep]:
    return [ScriptStep(kind="summary", summary=f"{role} assessment: recorded.")]


STATS_MODULE = '''"""Additional statistics helpers."""

from calc.core import total


def median(values):
    """Middle value of a sorted copy; mean of the middle pair when even."""
    ordered = sorted(values)
    if not ordered:
        return 0.0
    middle = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def spread(values):
    """Difference between the largest and smallest value."""
    ordered = sorted(values)
    if not ordered:
        return 0
    return ordered[-1] - ordered[0]


__all__ = ["median", "spread", "total"]
'''

INIT_WITH_STATS = '''"""calc package."""

from calc.core import average, total
from calc.stats import median, spread

__all__ = ["average", "total", "median", "spread"]
'''

# A refactor: the summation loop moves into its own module and core delegates.
SUMS_MODULE = '''"""Summation primitives, extracted from core."""


def add_all(values):
    """Sum every value, including negative ones."""
    return sum(values)
'''

CORE_DELEGATING = '''"""Aggregation helpers."""

from calc.sums import add_all


def total(values):
    """Sum every value, including negative ones."""
    return add_all(values)


def average(values):
    """Arithmetic mean; 0.0 for an empty sequence."""
    values = list(values)
    if not values:
        return 0.0
    return total(values) / len(values)
'''

# An API modification that keeps the old call signature working.
CORE_WITH_KEYWORD = '''"""Aggregation helpers."""


def total(values, *, absolute=False):
    """Sum every value. With absolute=True, sum magnitudes instead.

    The positional signature is unchanged, so existing callers keep working.
    """
    if absolute:
        return sum(abs(v) for v in values)
    return sum(values)


def average(values):
    """Arithmetic mean; 0.0 for an empty sequence."""
    values = list(values)
    if not values:
        return 0.0
    return total(values) / len(values)
'''

DOCS_USAGE_EXTENDED = """# Usage

```python
from calc.core import total, average

total([1, 2, 3])    # 6
total([5, -3])      # 2  — negative values are included
average([2, 4])     # 3.0
average([])         # 0.0
```

## Running the checks

```bash
python check.py
```
"""

README_EXTENDED = """# calc

A tiny aggregation library used as a SceneWorks qualification fixture.

- `calc.core.total` sums every value, including negatives.
- `calc.core.average` returns 0.0 for an empty sequence.

Run the check suite with `python check.py`. See [docs/usage.md](docs/usage.md).
"""


# ------------------------------------------------------------------- scenario


@dataclass(frozen=True)
class Scenario:
    key: str
    title: str
    description: str
    repo: str
    drive: str = DRIVE_FULL
    role_scripts: dict[str, list[ScriptStep]] = field(default_factory=dict)
    role_sequences: dict[str, list[list[ScriptStep]]] = field(default_factory=dict)

    # --- routing expectations -----------------------------------------
    expect_request_type: str | None = None
    expect_requires_implementation: bool | None = None
    expect_advisory_roles: frozenset[str] | None = None
    #: False for negative controls: the harness must *detect* wrong routing.
    expect_routing_correct: bool = True

    # --- implementation expectations ----------------------------------
    expect_files_changed: frozenset[str] = frozenset()
    forbid_files_changed: frozenset[str] = frozenset()
    #: True  → a result commit distinct from the base must exist.
    #: False → no result commit may exist.
    expect_result_commit: bool | None = None
    #: True  → the harness must observe files changed that nobody asked for.
    expect_unexpected_changes: bool | None = None

    # --- verification expectations ------------------------------------
    expect_tests_pass_at_base: bool | None = None
    expect_tests_pass_at_result: bool | None = None

    # --- review expectations ------------------------------------------
    expect_reviewer_detects_defect: bool | None = None
    expect_reviewer_false_approval: bool | None = None
    expect_min_repair_iterations: int | None = None
    expect_max_repair_iterations: int | None = None

    # --- process expectations -----------------------------------------
    expect_final_status: frozenset[str] = frozenset()
    expect_min_backend_failures: int | None = None
    expect_cancellation_honoured: bool | None = None
    expect_recovery_reported: bool | None = None
    #: Architect analysis must be present and non-empty.
    expect_architecture_present: bool | None = None

    #: Scenarios a release must pass. Negative controls are required too: if the
    #: harness stops detecting a seeded regression, qualification is worthless.
    required: bool = True


# ------------------------------------------------------------------ registry

_FIXED_CORE_ENGINEER = [
    _write("calc/core.py", CALC_CORE_FIXED),
    _commit("fix total() to include negative values"),
    ScriptStep(kind="summary", summary="Fixed total(); check.py now passes."),
]

SCENARIOS: tuple[Scenario, ...] = (
    # 1 -------------------------------------------------------------------
    Scenario(
        key="architecture-investigation",
        title="Architecture-only investigation (no code change)",
        description=(
            "Review how calc/ is layered and report whether the aggregation "
            "helpers belong in one module. Do not change any code."
        ),
        repo=CALC_HEALTHY.name,
        drive=DRIVE_ADVISORY,
        role_scripts={
            "triage": [ScriptStep(kind="summary", summary=triage_summary(
                request_type="architecture",
                requires_implementation=False,
                reasoning_summary="read-only architecture review",
            ))],
            "architect": _arch(
                "calc/core.py mixes summation and averaging. Splitting is "
                "optional; the current size does not justify it."
            ),
        },
        expect_request_type="architecture",
        expect_requires_implementation=False,
        expect_result_commit=False,
        expect_architecture_present=True,
        expect_final_status=frozenset({"READY_FOR_HUMAN"}),
    ),
    # 2 -------------------------------------------------------------------
    Scenario(
        key="bug-fix",
        title="Bug fix (seeded defect, verified by the repository's own checks)",
        description=(
            "calc.core.total() silently drops negative values, so total([5, -3]) "
            "returns 5 instead of 2. Fix it and make python check.py pass."
        ),
        repo=CALC_BUGGY.name,
        role_scripts={
            "architect": _arch("Fix the comprehension filter in total()."),
            "engineer": _FIXED_CORE_ENGINEER,
            "reviewer": _review("APPROVED", "total() now sums every value; checks pass."),
        },
        expect_requires_implementation=True,
        expect_files_changed=frozenset({"calc/core.py"}),
        forbid_files_changed=frozenset({"check.py"}),
        expect_result_commit=True,
        expect_tests_pass_at_base=False,
        expect_tests_pass_at_result=True,
        expect_reviewer_false_approval=False,
        expect_final_status=frozenset({"READY_FOR_HUMAN"}),
        expect_architecture_present=True,
    ),
    # 3 -------------------------------------------------------------------
    Scenario(
        key="small-feature",
        title="Small feature (one new module, existing checks stay green)",
        description=(
            "Add a calc.stats module exposing median() and spread(). Existing "
            "behaviour must not change."
        ),
        repo=CALC_HEALTHY.name,
        role_scripts={
            "architect": _arch("Add calc/stats.py; do not touch calc/core.py."),
            "engineer": [
                _write("calc/stats.py", STATS_MODULE),
                _commit("add calc.stats with median and spread"),
                ScriptStep(kind="summary", summary="Added calc/stats.py."),
            ],
            "reviewer": _review("APPROVED"),
        },
        expect_requires_implementation=True,
        expect_files_changed=frozenset({"calc/stats.py"}),
        forbid_files_changed=frozenset({"calc/core.py", "check.py"}),
        expect_result_commit=True,
        expect_tests_pass_at_base=True,
        expect_tests_pass_at_result=True,
        expect_final_status=frozenset({"READY_FOR_HUMAN"}),
    ),
    # 4 -------------------------------------------------------------------
    Scenario(
        key="multi-file-feature",
        title="Multi-file feature (new module plus package re-export)",
        description=(
            "Add calc.stats with median() and spread(), and re-export them from "
            "the calc package so callers can import them directly."
        ),
        repo=CALC_HEALTHY.name,
        role_scripts={
            "architect": _arch("Add calc/stats.py and update calc/__init__.py."),
            "engineer": [
                _write("calc/stats.py", STATS_MODULE),
                _write("calc/__init__.py", INIT_WITH_STATS),
                _commit("add calc.stats and re-export from the package"),
                ScriptStep(kind="summary", summary="Added stats module and re-exports."),
            ],
            "reviewer": _review("APPROVED"),
        },
        expect_requires_implementation=True,
        expect_files_changed=frozenset({"calc/stats.py", "calc/__init__.py"}),
        expect_result_commit=True,
        expect_tests_pass_at_result=True,
        expect_final_status=frozenset({"READY_FOR_HUMAN"}),
    ),
    # 5 -------------------------------------------------------------------
    Scenario(
        key="refactoring",
        title="Refactoring (structure changes, behaviour preserved)",
        description=(
            "Extract the summation primitive from calc/core.py into calc/sums.py "
            "and have core delegate to it. Behaviour must not change."
        ),
        repo=CALC_HEALTHY.name,
        role_scripts={
            "architect": _arch("Move the sum into calc/sums.py; core delegates."),
            "engineer": [
                _write("calc/sums.py", SUMS_MODULE),
                _write("calc/core.py", CORE_DELEGATING),
                _commit("extract summation into calc.sums"),
                ScriptStep(kind="summary", summary="Extracted add_all; core delegates."),
            ],
            "reviewer": _review("APPROVED", "Behaviour preserved; checks still pass."),
        },
        expect_files_changed=frozenset({"calc/sums.py", "calc/core.py"}),
        expect_result_commit=True,
        # The point of a refactor: the checks passed before and still pass.
        expect_tests_pass_at_base=True,
        expect_tests_pass_at_result=True,
        expect_final_status=frozenset({"READY_FOR_HUMAN"}),
    ),
    # 6 -------------------------------------------------------------------
    Scenario(
        key="api-modification",
        title="API modification (extended signature, callers unaffected)",
        description=(
            "Add an absolute=True keyword option to calc.core.total() without "
            "breaking the existing positional signature."
        ),
        repo=CALC_HEALTHY.name,
        role_scripts={
            "architect": _arch("Add a keyword-only parameter; keep positional use working."),
            "engineer": [
                _write("calc/core.py", CORE_WITH_KEYWORD),
                _commit("add absolute option to total()"),
                ScriptStep(kind="summary", summary="total() gained a keyword-only option."),
            ],
            "reviewer": _review("APPROVED", "Backward compatible; checks pass."),
        },
        expect_files_changed=frozenset({"calc/core.py"}),
        expect_result_commit=True,
        expect_tests_pass_at_base=True,
        expect_tests_pass_at_result=True,
        expect_final_status=frozenset({"READY_FOR_HUMAN"}),
    ),
    # 7 --- NEGATIVE CONTROL ----------------------------------------------
    Scenario(
        key="intentional-regression",
        title="NEGATIVE CONTROL — Engineer breaks a healthy repository",
        description=(
            "Make total() faster. (The scripted Engineer replaces summation with "
            "multiplication, breaking the repository's own checks, and the "
            "scripted Reviewer approves it anyway.)"
        ),
        repo=CALC_HEALTHY.name,
        role_scripts={
            "architect": _arch("Optimise the summation loop."),
            "engineer": [
                _write("calc/core.py", CALC_CORE_REGRESSED),
                _commit("optimise total()"),
                ScriptStep(kind="summary", summary="Rewrote total() as a loop."),
            ],
            # A reviewer that fails to notice. The harness must catch this.
            "reviewer": _review("APPROVED", "Looks fine to me."),
        },
        # The scenario passes when the harness sees the regression AND sees that
        # the reviewer approved broken work.
        expect_tests_pass_at_base=True,
        expect_tests_pass_at_result=False,
        expect_reviewer_false_approval=True,
        expect_result_commit=True,
        expect_files_changed=frozenset({"calc/core.py"}),
    ),
    # 8 -------------------------------------------------------------------
    Scenario(
        key="reviewer-detects-defect",
        title="Reviewer detects a seeded implementation defect",
        description=(
            "Make total() faster. (The Engineer breaks it; the Reviewer is "
            "expected to request changes rather than approve.)"
        ),
        repo=CALC_HEALTHY.name,
        role_scripts={
            "architect": _arch("Optimise the summation loop."),
            "engineer": [
                _write("calc/core.py", CALC_CORE_REGRESSED),
                _commit("optimise total()"),
                ScriptStep(kind="summary", summary="Rewrote total() as a loop."),
            ],
            "reviewer": _review(
                "CHANGES_REQUESTED",
                "total() now multiplies instead of summing; check.py fails.",
            ),
        },
        expect_reviewer_detects_defect=True,
        expect_reviewer_false_approval=False,
        expect_tests_pass_at_result=False,
        # Auto-repair keeps producing the same broken change, so the loop stops
        # at the configured bound and hands the task back to the human.
        expect_final_status=frozenset({"CHANGES_REQUESTED"}),
        expect_min_repair_iterations=1,
    ),
    # 9 -------------------------------------------------------------------
    Scenario(
        key="reviewer-engineer-repair-loop",
        title="Reviewer → Engineer repair loop converges",
        description=(
            "Fix total() so negative values are included. (The first Engineer "
            "attempt is wrong, the Reviewer requests changes, the second attempt "
            "is correct and is approved.)"
        ),
        repo=CALC_BUGGY.name,
        role_sequences={
            # First attempt: still wrong (drops negatives a different way).
            "engineer": [
                [
                    _write("calc/core.py",
                           '"""Aggregation helpers."""\n\n\ndef total(values):\n'
                           '    """Sum every value, including negative ones."""\n'
                           "    return sum(max(v, 0) for v in values)\n\n\n"
                           "def average(values):\n"
                           '    """Arithmetic mean; 0.0 for an empty sequence."""\n'
                           "    values = list(values)\n"
                           "    if not values:\n"
                           "        return 0.0\n"
                           "    return total(values) / len(values)\n"),
                    _commit("first attempt at total()"),
                    ScriptStep(kind="summary", summary="Clamped negatives to zero."),
                ],
                # Repair: correct.
                _FIXED_CORE_ENGINEER,
            ],
            "reviewer": [
                _review("CHANGES_REQUESTED",
                        "max(v, 0) still discards negative values; check.py fails."),
                _review("APPROVED", "total() now sums every value; checks pass."),
            ],
        },
        role_scripts={"architect": _arch("Fix the filter in total().")},
        expect_reviewer_detects_defect=True,
        expect_min_repair_iterations=1,
        expect_max_repair_iterations=2,
        expect_tests_pass_at_base=False,
        expect_tests_pass_at_result=True,
        expect_reviewer_false_approval=False,
        expect_final_status=frozenset({"READY_FOR_HUMAN"}),
        expect_result_commit=True,
    ),
    # 10 ------------------------------------------------------------------
    Scenario(
        key="ambiguous-requirement",
        title="Ambiguous requirement routes through Product",
        description=(
            "Make the aggregation helpers better for our users. (Deliberately "
            "vague: scope and acceptance criteria are undefined.)"
        ),
        repo=CALC_HEALTHY.name,
        drive=DRIVE_ADVISORY,
        role_scripts={
            "triage": [ScriptStep(kind="summary", summary=triage_summary(
                request_type="product_question",
                use_product=True,
                use_architect=True,
                requires_implementation=False,
                reasoning_summary="scope undefined; Product must define it first",
            ))],
            "product": _advisory("Product"),
            "architect": _arch("Cannot design until the requirement is defined."),
        },
        expect_request_type="product_question",
        expect_requires_implementation=False,
        expect_advisory_roles=frozenset({"product"}),
        expect_result_commit=False,
        expect_final_status=frozenset({"READY_FOR_HUMAN"}),
    ),
    # 11 ------------------------------------------------------------------
    Scenario(
        key="no-implementation-needed",
        title="Request that should NOT produce an implementation",
        description=(
            "Should we migrate calc to numpy for performance? Weigh the "
            "dependency cost against the benefit. Do not implement anything."
        ),
        repo=CALC_HEALTHY.name,
        drive=DRIVE_ADVISORY,
        role_scripts={
            "triage": [ScriptStep(kind="summary", summary=triage_summary(
                request_type="technology_decision",
                use_cto=True,
                use_architect=True,
                requires_implementation=False,
                reasoning_summary="a decision is requested, not an implementation",
            ))],
            "cto": _advisory("CTO"),
            "architect": _arch("numpy is unjustified at this scale."),
        },
        expect_request_type="technology_decision",
        expect_requires_implementation=False,
        expect_advisory_roles=frozenset({"cto"}),
        # The whole point: nothing may be implemented or committed.
        expect_result_commit=False,
        expect_files_changed=frozenset(),
        expect_final_status=frozenset({"READY_FOR_HUMAN"}),
    ),
    # 12 ------------------------------------------------------------------
    Scenario(
        key="documentation-only",
        title="Documentation-only task (no source file may change)",
        description=(
            "Document that total() includes negative values and that average() "
            "returns 0.0 for an empty sequence. Update the README and usage docs."
        ),
        repo=CALC_HEALTHY.name,
        role_scripts={
            "architect": _arch("Documentation change only; no source edits."),
            "engineer": [
                _write("docs/usage.md", DOCS_USAGE_EXTENDED),
                _write("README.md", README_EXTENDED),
                _commit("document total() and average() semantics"),
                ScriptStep(kind="summary", summary="Updated README and docs/usage.md."),
            ],
            "reviewer": _review("APPROVED", "Docs only; no code touched."),
        },
        expect_files_changed=frozenset({"docs/usage.md", "README.md"}),
        forbid_files_changed=frozenset({
            "calc/core.py", "calc/__init__.py", "check.py",
        }),
        expect_result_commit=True,
        expect_tests_pass_at_result=True,
        expect_final_status=frozenset({"READY_FOR_HUMAN"}),
    ),
    # 13 ------------------------------------------------------------------
    Scenario(
        key="performance-task",
        title="Performance-oriented task routes through the Technical Expert",
        description=(
            "total() is called in a hot loop over millions of values. Assess "
            "whether the current implementation is an algorithmic bottleneck."
        ),
        repo=CALC_HEALTHY.name,
        drive=DRIVE_ADVISORY,
        role_scripts={
            "triage": [ScriptStep(kind="summary", summary=triage_summary(
                request_type="technical_investigation",
                use_technical_expert=True,
                use_architect=True,
                requires_implementation=False,
                reasoning_summary="algorithmic/performance assessment requested",
            ))],
            "technical_expert": _advisory("Technical Expert"),
            "architect": _arch("sum() is already the C-level primitive."),
        },
        expect_request_type="technical_investigation",
        expect_advisory_roles=frozenset({"technical_expert"}),
        expect_requires_implementation=False,
        expect_result_commit=False,
        expect_final_status=frozenset({"READY_FOR_HUMAN"}),
    ),
    # 14 ------------------------------------------------------------------
    Scenario(
        key="failed-execution",
        title="Failed execution surfaces as FAILED with no commit",
        description="Fix total(). (The scripted Engineer backend fails.)",
        repo=CALC_BUGGY.name,
        role_scripts={
            "architect": _arch("Fix the filter in total()."),
            "engineer": [
                ScriptStep(kind="emit", type="agent.message",
                           payload={"text": "starting implementation"}),
                ScriptStep(kind="fail", error="agent runtime crashed"),
            ],
        },
        expect_final_status=frozenset({"FAILED"}),
        expect_min_backend_failures=1,
        expect_result_commit=False,
        # A failed implementation must not be reviewed or approved.
        expect_reviewer_detects_defect=None,
        expect_tests_pass_at_base=False,
    ),
    # 15 ------------------------------------------------------------------
    Scenario(
        key="cancellation",
        title="Cancellation stops the workflow without producing a commit",
        description="Analyse calc/ thoroughly. (Cancelled while the Architect runs.)",
        repo=CALC_HEALTHY.name,
        drive=DRIVE_CANCEL,
        role_scripts={
            "architect": [
                ScriptStep(kind="emit", type="agent.message",
                           payload={"text": "analysing"}),
                ScriptStep(kind="sleep", seconds=30),
                ScriptStep(kind="summary", summary="should never be reached"),
            ],
        },
        expect_final_status=frozenset({"CANCELLED"}),
        expect_cancellation_honoured=True,
        expect_result_commit=False,
    ),
    # 16 ------------------------------------------------------------------
    Scenario(
        key="restart-recovery",
        title="Restart reports what survived and what was interrupted",
        description=(
            "Fix total(). (SceneWorks is torn down mid-execution and rebuilt "
            "against the same database.)"
        ),
        repo=CALC_BUGGY.name,
        drive=DRIVE_RESTART,
        role_scripts={
            "architect": [
                ScriptStep(kind="emit", type="agent.message",
                           payload={"text": "analysing"}),
                ScriptStep(kind="sleep", seconds=30),
                ScriptStep(kind="summary", summary="should never be reached"),
            ],
        },
        expect_recovery_reported=True,
        expect_result_commit=False,
    ),
    # 17 --- NEGATIVE CONTROL ---------------------------------------------
    Scenario(
        key="unnecessary-change",
        title="NEGATIVE CONTROL — unnecessary changes are detected",
        description=(
            "Add calc.stats with median() and spread(). (The scripted Engineer "
            "also rewrites check.py and README.md, which nobody asked for.)"
        ),
        repo=CALC_HEALTHY.name,
        role_scripts={
            "architect": _arch("Add calc/stats.py only."),
            "engineer": [
                _write("calc/stats.py", STATS_MODULE),
                # Neither of these was requested.
                _write("README.md", README_EXTENDED),
                _write("calc/core.py", CORE_WITH_KEYWORD),
                _commit("add stats (and unrelated edits)"),
                ScriptStep(kind="summary", summary="Added stats module."),
            ],
            "reviewer": _review("APPROVED"),
        },
        expect_files_changed=frozenset({"calc/stats.py"}),
        # The harness must notice the two files that were not asked for.
        expect_unexpected_changes=True,
        expect_result_commit=True,
    ),
    # 18 --- NEGATIVE CONTROL ---------------------------------------------
    Scenario(
        key="incorrect-triage",
        title="NEGATIVE CONTROL — incorrect routing is detected",
        description=(
            "Which of our two aggregation helpers do users prefer? (A pure "
            "product question. The scripted Triage wrongly routes it to "
            "implementation.)"
        ),
        repo=CALC_HEALTHY.name,
        role_scripts={
            "triage": [ScriptStep(kind="summary", summary=triage_summary(
                request_type="feature",
                requires_implementation=True,
                reasoning_summary="wrongly classified as a feature",
            ))],
            "architect": _arch("There is nothing to build here."),
            "engineer": [
                ScriptStep(kind="summary", summary="Nothing to implement."),
            ],
            "reviewer": _review("APPROVED", "Empty diff."),
        },
        # What triage *should* have said for this request.
        expect_request_type="product_question",
        expect_requires_implementation=False,
        # ...and the harness must report that routing was wrong.
        expect_routing_correct=False,
        # The consequence of the misrouting: a full implementation cycle ran and
        # produced nothing, because there was nothing to build. Asserting this
        # keeps the scenario from resting on the routing check alone.
        expect_result_commit=False,
        expect_files_changed=frozenset(),
        expect_final_status=frozenset({"READY_FOR_HUMAN"}),
    ),
)


SCENARIOS_BY_KEY: dict[str, Scenario] = {s.key: s for s in SCENARIOS}

#: Keys a release must pass before qualification reports PASS.
REQUIRED_KEYS: list[str] = [s.key for s in SCENARIOS if s.required]

#: A fast subset for push-triggered CI. Chosen to cover one positive
#: implementation path, one review path, and one negative control, so a broken
#: harness is caught quickly.
SMOKE_KEYS: list[str] = [
    "bug-fix",
    "no-implementation-needed",
    "reviewer-detects-defect",
    "intentional-regression",
]


def select(keys: list[str] | None = None, smoke: bool = False) -> list[Scenario]:
    if smoke:
        return [SCENARIOS_BY_KEY[k] for k in SMOKE_KEYS]
    if not keys:
        return list(SCENARIOS)
    unknown = [k for k in keys if k not in SCENARIOS_BY_KEY]
    if unknown:
        raise KeyError(f"unknown scenario(s): {', '.join(unknown)}")
    return [SCENARIOS_BY_KEY[k] for k in keys]
