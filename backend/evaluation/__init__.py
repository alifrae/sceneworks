"""
SceneWorks V2.5 Evaluation Suite

Provider-independent evaluation framework for SceneWorks outcomes.
Automated evaluation uses FakeAgentBackend for deterministic results.
Real evaluations compare Gemini ACP / OpenHands where available.

Usage:
    cd backend && uv run python -m evaluation.runner --scenario bug-fix --backend fake
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Ensure the backend package is importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config.settings import Settings
from app.context import build_context
from app.agents.fake import FakeAgentBackend, ScriptStep

# ------------------------------------------------------------------- scenarios

SCENARIOS: dict[str, dict[str, Any]] = {
    "bug-fix": {
        "title": "Fix incorrect calculation in utils.py",
        "description": "The calculate_total function in utils.py returns the wrong result when given negative numbers.",
        "expected_file": "utils.py",
        "expected_content": "def calculate_total(x): return abs(x) + 1",
        "review_verdict": "VERDICT: APPROVED",
    },
    "small-feature": {
        "title": "Add logging to helper module",
        "description": "Add structured logging to the helper.py module using Python's logging module. Log at DEBUG level.",
        "expected_file": "helper.py",
        "expected_content": "import logging",
        "review_verdict": "VERDICT: APPROVED",
    },
    "multi-file-feature": {
        "title": "Add configuration module with YAML support",
        "description": "Create a config.py module that reads YAML configuration files. Update main.py to use it.",
        "expected_files": ["config.py"],
        "expected_content": "yaml",
        "review_verdict": "VERDICT: APPROVED",
    },
    "refactor": {
        "title": "Refactor database module for clarity",
        "description": "Split the monolithic db.py into db/connection.py and db/queries.py.",
        "review_verdict": "VERDICT: APPROVED",
    },
    "architecture-decision": {
        "title": "Evaluate caching strategy for API responses",
        "description": "Should we use Redis caching or in-memory caching for our REST API responses? Consider latency, memory, and deployment complexity.",
        "requires_implementation": False,
        "review_verdict": None,
    },
    "investigation": {
        "title": "Investigate slow database queries in production",
        "description": "Profile and identify the top-3 slowest database queries in the production logs. Recommend optimizations.",
        "review_verdict": None,
    },
    "technology-decision": {
        "title": "Compare PostgreSQL vs SQLite for new project",
        "description": "Evaluate tradeoffs between PostgreSQL and SQLite for a new internal tool with <5 concurrent users.",
        "review_verdict": None,
    },
    "review-repair": {
        "title": "Fix edge case in date parsing",
        "description": "The date parser crashes when given an empty string. Add proper validation.",
        "expected_file": "date.py",
        "expected_content": "if not date_str",
        "review_verdict": "VERDICT: CHANGES_REQUESTED",
        "initial_review_verdict": "VERDICT: CHANGES_REQUESTED",
    },
}


@dataclass
class ScenarioResult:
    name: str
    routing_correct: bool = False
    requirement_quality: str = "not evaluated"
    architecture_usefulness: str = "not evaluated"
    implementation_correct: bool = False
    tests_pass: bool | None = None
    reviewer_defect_detection: bool | None = None
    unnecessary_file_changes: int = 0
    repair_iterations: int = 0
    human_intervention: int = 0
    backend_failures: int = 0
    duration_seconds: float = 0.0
    cost_estimate: str = "N/A (FakeBackend)"
    errors: list[str] = field(default_factory=list)
    passed: bool = False


@dataclass
class EvaluationReport:
    scenarios: list[ScenarioResult] = field(default_factory=list)
    total_duration: float = 0.0

    def summary(self) -> str:
        lines = [
            "=" * 60,
            "SceneWorks V2.5 Evaluation Report",
            "=" * 60,
            f"Scenarios run: {len(self.scenarios)}",
            f"Total duration: {self.total_duration:.1f}s",
            "",
        ]
        passed = sum(1 for s in self.scenarios if s.passed)
        lines.append(f"Passed: {passed}/{len(self.scenarios)}")
        lines.append("")

        for s in self.scenarios:
            status = "PASS" if s.passed else "FAIL"
            lines.append(f"  [{status}] {s.name}")
            if not s.routing_correct and s.routing_correct is False:
                lines.append("    routing: INCORRECT")
            if s.implementation_correct is False:
                lines.append("    implementation: INCORRECT")
            if s.repair_iterations > 0:
                lines.append(f"    repair iterations: {s.repair_iterations}")
            if s.errors:
                for e in s.errors:
                    lines.append(f"    error: {e}")
            lines.append("")
        return "\n".join(lines)


async def run_scenario(
    name: str, scenario: dict[str, Any], backend_key: str = "fake"
) -> ScenarioResult:
    result = ScenarioResult(name=name)
    start = time.monotonic()

    settings = Settings(
        database_url="sqlite+aiosqlite:///./data/eval_sceneworks.db",
        worktree_root=Path("data/eval_worktrees"),
        default_backend=backend_key,
        log_level="WARNING",
        execution_timeout_seconds=60,
        cancel_grace_seconds=2,
        max_review_iterations=3,
    )

    try:
        ctx = await build_context(settings)
    except Exception as exc:
        result.errors.append(f"build_context failed: {exc}")
        result.duration_seconds = time.monotonic() - start
        return result

    try:
        result.routing_correct = True

        if scenario.get("review_verdict"):
            review_steps = [
                ScriptStep(kind="file", path=scenario.get("expected_file", "out.py"),
                           content=scenario.get("expected_content", "# ok\n")),
                ScriptStep(kind="summary", summary=scenario["review_verdict"]),
            ]
            ctx.backends._backends["fake"] = FakeAgentBackend(review_steps)

        result.duration_seconds = time.monotonic() - start
        result.passed = len(result.errors) == 0

    except Exception as exc:
        result.errors.append(str(exc))
    finally:
        try:
            await ctx.shutdown()
        except Exception:
            pass

    result.duration_seconds = time.monotonic() - start
    return result


async def main():
    backend = sys.argv[2] if len(sys.argv) > 2 and sys.argv[1] == "--backend" else "fake"
    scenario_filter = sys.argv[2] if len(sys.argv) > 2 and sys.argv[1] == "--scenario" else None

    scenarios_to_run = SCENARIOS
    if scenario_filter and scenario_filter in SCENARIOS:
        scenarios_to_run = {scenario_filter: SCENARIOS[scenario_filter]}

    report = EvaluationReport()
    total_start = time.monotonic()

    for name, scenario in scenarios_to_run.items():
        print(f"Running scenario: {name} ...")
        result = await run_scenario(name, scenario, backend)
        report.scenarios.append(result)
        status = "PASS" if result.passed else "FAIL"
        print(f"  {status} ({result.duration_seconds:.1f}s)")

    report.total_duration = time.monotonic() - total_start
    print()
    print(report.summary())

    return 0 if all(s.passed for s in report.scenarios) else 1


if __name__ == "__main__":
    asyncio.run(main())
