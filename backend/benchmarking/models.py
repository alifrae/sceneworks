"""Provider-neutral productivity benchmark contracts (WP9).

A benchmark is not a release qualification run. A task implementation may fail
and still be valid benchmark evidence. Conversely, a trial that could not run
must never be treated as an engineering failure or success.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class TrialVerdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"


class BenchmarkStatus(str, Enum):
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"


class BenchmarkTask(BaseModel):
    """One controlled engineering task executed from a pinned repository state."""

    key: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9._-]+$")
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(min_length=1)
    repository_path: Path
    base_ref: str = Field(default="HEAD", min_length=1)
    verification_commands: list[str] = Field(min_length=1)
    baseline_expectation: Literal["must_fail", "must_pass", "any"] = "must_fail"
    architecture_context_paths: list[str] = Field(default_factory=list)
    expected_changed_files: list[str] = Field(default_factory=list)
    forbidden_changed_files: list[str] = Field(default_factory=list)
    engineering_contract: dict[str, list[str]] = Field(default_factory=dict)
    timeout_seconds: int = Field(default=3600, ge=60, le=21600)

    @model_validator(mode="after")
    def _nonempty_commands(self) -> "BenchmarkTask":
        if any(not command.strip() for command in self.verification_commands):
            raise ValueError("verification_commands may not contain empty commands")
        return self


class BenchmarkManifest(BaseModel):
    schema_version: Literal[1] = 1
    name: str = Field(min_length=1, max_length=200)
    backend: str = Field(default="gemini_acp", min_length=1)
    repeats: int = Field(default=1, ge=1, le=20)
    tasks: list[BenchmarkTask] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_keys(self) -> "BenchmarkManifest":
        keys = [task.key for task in self.tasks]
        if len(keys) != len(set(keys)):
            raise ValueError("benchmark task keys must be unique")
        return self


class CommandResult(BaseModel):
    command: str
    passed: bool
    returncode: int | None = None
    duration_seconds: float = 0.0
    output_tail: str = ""
    timed_out: bool = False


class TrialResult(BaseModel):
    task_key: str
    mode: Literal["sceneworks", "direct"]
    repeat: int
    verdict: TrialVerdict = TrialVerdict.BLOCKED
    blocker: str | None = None
    resolved_base_commit: str | None = None
    result_commit: str | None = None
    final_task_status: str | None = None
    duration_seconds: float = 0.0
    verification: list[CommandResult] = Field(default_factory=list)
    quality_gate_passed: bool | None = None
    files_changed: list[str] = Field(default_factory=list)
    expected_files_missing: list[str] = Field(default_factory=list)
    forbidden_files_changed: list[str] = Field(default_factory=list)
    human_interventions: int = 0
    agent_executions: int = 0
    architect_executions: int = 0
    engineer_executions: int = 0
    reviewer_executions: int = 0
    review_iterations: int = 0
    backend_failures: int = 0
    execution_targets: list[dict[str, Any]] = Field(default_factory=list)
    unsupported_metrics: dict[str, str] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)


class PairComparison(BaseModel):
    task_key: str
    repeat: int
    outcome: Literal[
        "both_pass",
        "sceneworks_only_pass",
        "direct_only_pass",
        "neither_pass",
        "incomplete",
    ]
    sceneworks_seconds: float | None = None
    direct_seconds: float | None = None
    elapsed_ratio_sceneworks_over_direct: float | None = None
    human_intervention_delta: int | None = None
    agent_execution_delta: int | None = None


class ModeAggregate(BaseModel):
    mode: Literal["sceneworks", "direct"]
    trial_count: int
    measured_trial_count: int
    pass_count: int
    fail_count: int
    blocked_count: int
    success_rate: float | None = None
    median_success_seconds: float | None = None
    mean_human_interventions: float | None = None
    mean_agent_executions: float | None = None
    backend_failures: int = 0


class BenchmarkReport(BaseModel):
    schema: Literal["sceneworks.productivity-benchmark/1"] = (
        "sceneworks.productivity-benchmark/1"
    )
    manifest_name: str
    backend: str
    started_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    finished_at: str | None = None
    status: BenchmarkStatus = BenchmarkStatus.INCOMPLETE
    trials: list[TrialResult] = Field(default_factory=list)
    comparisons: list[PairComparison] = Field(default_factory=list)
    aggregates: list[ModeAggregate] = Field(default_factory=list)
    environment: dict[str, Any] = Field(default_factory=dict)

    def finalize(self, expected_trials: int) -> "BenchmarkReport":
        from benchmarking.scoring import build_aggregates

        complete = (
            len(self.trials) == expected_trials
            and all(trial.verdict is not TrialVerdict.BLOCKED for trial in self.trials)
        )
        self.status = BenchmarkStatus.COMPLETE if complete else BenchmarkStatus.INCOMPLETE
        self.aggregates = build_aggregates(self.trials)
        self.finished_at = datetime.now(timezone.utc).isoformat()
        return self
