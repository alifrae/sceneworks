"""Provider-neutral productivity benchmark contracts.

A benchmark is not a release qualification run. A task implementation may fail
and still be valid benchmark evidence. Conversely, a trial that could not run
must never be treated as an engineering failure or success.

WP12 adds a separate adoption gate. A complete benchmark report answers
"do we have comparable evidence?"; the adoption gate answers "is that evidence
good enough to start dogfooding SceneWorks on PCS?". Keeping those decisions
separate prevents a complete-but-bad benchmark from being mistaken for success.
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


class AdoptionVerdict(str, Enum):
    READY_FOR_PILOT = "READY_FOR_PILOT"
    NOT_READY = "NOT_READY"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class AdoptionGatePolicy(BaseModel):
    """Explicit, configurable PCS adoption thresholds.

    Defaults are intentionally conservative rather than aspirational. They are
    suitable for the first PCS gate: at least five historical tasks, three
    repeats, complete paired evidence, no backend failures, SceneWorks quality
    no worse than the direct-agent baseline, and bounded orchestration overhead.
    A project may choose stricter thresholds in its benchmark manifest.
    """

    enabled: bool = True
    min_tasks: int = Field(default=5, ge=1, le=100)
    min_repeats: int = Field(default=3, ge=1, le=20)
    require_complete_pairs: bool = True
    min_sceneworks_success_rate: float = Field(default=0.8, ge=0.0, le=1.0)
    max_success_rate_regression: float = Field(default=0.0, ge=0.0, le=1.0)
    max_median_time_ratio: float = Field(default=2.0, ge=0.1, le=20.0)
    max_mean_human_interventions: float = Field(default=1.0, ge=0.0, le=20.0)
    max_backend_failures: int = Field(default=0, ge=0, le=1000)


class GateCheck(BaseModel):
    key: str
    passed: bool
    observed: Any = None
    required: Any = None
    detail: str = ""


class AdoptionGateResult(BaseModel):
    verdict: AdoptionVerdict
    checks: list[GateCheck] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    summary: str = ""


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
    # Human/audit provenance for historical-corpus curation. The benchmark
    # runner never reveals this to the agent prompt and never uses it to score.
    historical_fix_ref: str | None = None
    historical_source: str | None = None

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
    adoption_gate: AdoptionGatePolicy | None = None

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
    adoption_gate: AdoptionGateResult | None = None

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
