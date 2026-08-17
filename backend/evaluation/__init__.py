"""SceneWorks qualification suite (WP1).

A provider-independent engineering **outcome** evaluation system. It evaluates
SceneWorks itself — routing, architecture gating, implementation provenance,
review lifecycle, repair convergence, cancellation and restart behaviour — not
its individual components.

    cd backend
    uv run python -m evaluation --help

What replaced what: the V2.5 module that lived here reported PASS whenever
``build_context()`` did not raise, printing "implementation: INCORRECT" beside
every PASS. It invoked no workflow. See docs/wp0-baseline-audit.md (F1, F2) for
the recorded evidence, and docs/qualification.md for the current contract.

Layout:

    outcomes.py   verdicts, measurements, report model
    scenarios.py  scenario definitions and their expectations
    refrepo.py    deterministic reference repositories with their own checks
    harness.py    drives real workflows and measures real outcomes
    checks.py     expectations x observations -> checks
    report.py     human-readable summary
    live.py       optional live qualification against a real repository
    cli.py        entry point and documented exit codes
"""

from __future__ import annotations

from evaluation.outcomes import (
    Check,
    Observations,
    QualificationReport,
    ScenarioResult,
    UNSUPPORTED_METRICS,
    Verdict,
)
from evaluation.scenarios import (
    REQUIRED_KEYS,
    SCENARIOS,
    SCENARIOS_BY_KEY,
    SMOKE_KEYS,
    Scenario,
    select,
)

__all__ = [
    "Check",
    "Observations",
    "QualificationReport",
    "REQUIRED_KEYS",
    "SCENARIOS",
    "SCENARIOS_BY_KEY",
    "SMOKE_KEYS",
    "Scenario",
    "ScenarioResult",
    "UNSUPPORTED_METRICS",
    "Verdict",
    "select",
]
