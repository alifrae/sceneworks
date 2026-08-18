"""Qualification harness.

Drives a scenario through the **real** SceneWorks workflow — real LangGraph
graph, real execution engine, real Git worktrees, real database, real event
store — and measures what actually happened.

What "real" excludes: the agent backend. A scripted ``FakeAgentBackend`` stands
in for the model so outcomes are deterministic. That is the correct seam: it
makes SceneWorks' orchestration, routing, Git handling, review lifecycle and
recovery the thing under test, rather than a model's mood. Whether a *model*
produces good code is what live qualification mode is for (see ``live.py``).

Nothing here writes to a human working tree. Every scenario builds its own
throwaway reference repository under a temporary directory.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
import time
from dataclasses import replace
from pathlib import Path

from app import __version__
from app.agents.fake import FakeAgentBackend
from app.config.settings import Settings
from app.context import AppContext, build_context
from app.domain.task_states import TaskStatus
from app.models import Execution, Project, Task

from evaluation import checks as check_builder
from evaluation.outcomes import Observations, ScenarioResult, UNSUPPORTED_METRICS
from evaluation.refrepo import REPOS, RepoError, materialize, run_check
from evaluation.scenarios import (
    DRIVE_ADVISORY,
    DRIVE_CANCEL,
    DRIVE_FULL,
    DRIVE_RESTART,
    Scenario,
)

logger = logging.getLogger("sceneworks.qualification")

#: Terminal task states the harness stops waiting on.
TERMINAL = {
    TaskStatus.READY_FOR_HUMAN.value,
    TaskStatus.ACCEPTED.value,
    TaskStatus.REJECTED.value,
    TaskStatus.FAILED.value,
    TaskStatus.CANCELLED.value,
    # Auto-repair exhausted: the task waits for a human, which is terminal for
    # the purposes of one scenario run.
    TaskStatus.CHANGES_REQUESTED.value,
}

#: Per-scenario budget. A scenario takes ~13 s in isolation, but the full suite
#: creates 19 repositories, ~70 worktrees and ~70 subprocesses back to back, and
#: `git worktree add` on Windows degrades sharply under that load — a scenario
#: that runs in 13 s alone was observed exceeding 120 s late in a full run.
#: Generous enough to absorb that, tight enough to still catch a genuine hang.
DEFAULT_TIMEOUT = 300.0


class HarnessError(RuntimeError):
    """A precondition of the harness failed — the scenario is BLOCKED, not FAIL."""


# --------------------------------------------------------------------- runner


#: Metrics that stop being measurable once a real agent backend is used: nothing
#: in the run is scripted, so an expectation about exact behaviour describes the
#: model rather than SceneWorks.
LIVE_UNSUPPORTED_METRICS = {
    "reviewer_false_approval": (
        "Requires a scripted reviewer verdict. A real reviewer's verdict is a "
        "measurement of the model, not of SceneWorks."
    ),
    "repair_iterations": (
        "A real agent may converge in any number of iterations; a bound would "
        "assert model behaviour."
    ),
}


async def run_scenario(
    scenario: Scenario,
    workdir: Path,
    timeout: float = DEFAULT_TIMEOUT,
    backend: str = "fake",
) -> ScenarioResult:
    """Drive one scenario and return its evaluated result.

    `backend` selects the agent backend. The default `fake` gives deterministic
    outcomes and is what release qualification uses. Any other value runs the
    real provider: the scenario's `role_scripts` are then irrelevant, and a
    provider that is not actually usable yields BLOCKED — never PASS.
    """
    result = ScenarioResult(key=scenario.key, title=scenario.title)
    result.unsupported = sorted(UNSUPPORTED_METRICS)
    if backend != "fake":
        result.unsupported = sorted(
            set(result.unsupported) | set(LIVE_UNSUPPORTED_METRICS)
        )
    obs = result.observations
    started = time.monotonic()

    root = workdir / scenario.key
    root.mkdir(parents=True, exist_ok=True)

    if backend != "fake" and not scenario.live_capable:
        result.blockers.append(
            f"scenario {scenario.key!r} is scripted-only and cannot be evaluated "
            f"against the real {backend!r} backend: it works by scripting a "
            "specific agent behaviour, which a real model cannot be made to "
            "reproduce on cue"
        )
        obs.duration_seconds = round(time.monotonic() - started, 3)
        return result.finalize()

    context: AppContext | None = None
    try:
        repo_def = REPOS[scenario.repo]
        repo_path, base_commit = materialize(repo_def, root / "repo")
        obs.repository_path = str(repo_path)
        obs.base_commit = base_commit
        obs.test_command = "python check.py"

        # Baseline measurement: does the reference repository pass its own
        # checks before SceneWorks touches it? Without this, "tests pass at the
        # result" would not distinguish a fix from a repository that was never
        # broken.
        base_ok, base_output = run_check(repo_path)
        obs.tests_pass_at_base = base_ok
        if repo_def.broken_at_base and base_ok:
            raise HarnessError(
                f"reference repo {repo_def.name} declares broken_at_base=True but "
                f"its checks pass at the base commit; the fixture is wrong, so "
                f"'tests now pass' would prove nothing"
            )
        if not repo_def.broken_at_base and not base_ok:
            raise HarnessError(
                f"reference repo {repo_def.name} declares broken_at_base=False but "
                f"its checks already fail at the base commit; a regression could "
                f"not be distinguished from the starting state. Output: "
                f"{base_output[:300]}"
            )

        settings = _settings_for(root, backend=backend)
        context = await build_context(settings)

        if backend == "fake":
            context.backends.register(
                "fake",
                FakeAgentBackend(
                    role_scripts=scenario.role_scripts,
                    role_sequences=scenario.role_sequences,
                ),
            )
        else:
            # A real provider must prove it can execute before the scenario runs.
            # Reporting PASS or FAIL on a provider that was never usable would be
            # exactly the fabrication WP1 exists to prevent.
            health = await context.backends.get(backend).health()
            if not health.available:
                raise HarnessError(
                    f"backend {backend!r} is not available: {health.detail}"
                )
            obs.backend_detail = health.detail
            obs.backend_version = health.version

        project, task = await _seed(context, scenario, repo_path)
        obs.project_id, obs.task_id = project.id, task.id
        await _seed_memories(context, scenario, project.id)
        await _seed_policy(context, scenario, project.id)

        if scenario.drive == DRIVE_RESTART:
            context = await _drive_restart(context, settings, task.id, obs, timeout)
        elif scenario.drive == DRIVE_CANCEL:
            await _drive_cancel(context, task.id, obs, timeout)
        elif scenario.drive == DRIVE_ADVISORY:
            await _drive_advisory(context, task.id, timeout)
        elif scenario.drive == DRIVE_FULL:
            await _drive_full(context, task.id, obs, timeout)
        else:  # pragma: no cover - guarded by scenario definitions
            raise HarnessError(f"unknown drive mode {scenario.drive!r}")

        await _observe(context, scenario, task.id, repo_path, obs)

    except (HarnessError, RepoError) as exc:
        result.blockers.append(str(exc))
    except asyncio.TimeoutError:
        result.blockers.append(
            f"scenario exceeded {timeout}s; SceneWorks did not reach a terminal state"
        )
    except Exception as exc:  # noqa: BLE001 - a harness crash must not be a PASS
        logger.exception("harness crashed running %s", scenario.key)
        result.blockers.append(f"harness error: {type(exc).__name__}: {exc}")
    finally:
        obs.duration_seconds = round(time.monotonic() - started, 3)
        if context is not None:
            try:
                await context.shutdown()
            except Exception:  # noqa: BLE001
                logger.warning("context shutdown failed for %s", scenario.key)

    if not result.blockers:
        result.checks = check_builder.build(scenario, obs)
    return result.finalize()


# ------------------------------------------------------------------ scaffolding


def _settings_for(root: Path, backend: str = "fake") -> Settings:
    # A real provider driving a real model needs a real budget; a scripted run
    # measured in seconds does not, and a generous timeout there would turn a
    # hung scenario into a 90-minute stall.
    execution_timeout = 90 if backend == "fake" else 1800
    return Settings(
        database_url=f"sqlite+aiosqlite:///{(root / 'qualification.db').as_posix()}",
        worktree_root=root / "worktrees",
        checkpoint_db_path=str(root / "checkpoints.db"),
        default_backend=backend,
        log_level="ERROR",
        execution_timeout_seconds=execution_timeout,
        cancel_grace_seconds=2 if backend == "fake" else 30,
        max_review_iterations=3,
    )


async def _seed(
    context: AppContext, scenario: Scenario, repo_path: Path,
) -> tuple[Project, Task]:
    async with context.engine_factory() as session:
        project = Project(
            name=f"qualification-{scenario.key}",
            description=f"Qualification fixture for {scenario.key}",
            repository_path=str(repo_path),
            default_branch="main",
            test_commands=["python check.py"],
        )
        session.add(project)
        await session.commit()
        await session.refresh(project)

        task = Task(
            project_id=project.id,
            title=scenario.title,
            description=scenario.description,
            status=TaskStatus.NEW.value,
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)
        return project, task


async def _seed_memories(
    context: AppContext, scenario: Scenario, project_id: int,
) -> None:
    """Create the scenario's project memories before the workflow starts.

    Goes through MemoryService rather than inserting rows, so a scenario cannot
    seed something the service itself would reject — and so a seeded "accepted"
    memory is accepted by the same code path a human review uses.
    """
    for spec in scenario.seed_memories:
        await context.memory.create(project_id=project_id, **spec)


async def _seed_policy(
    context: AppContext, scenario: Scenario, project_id: int,
) -> None:
    """Configure the scenario's project policy (WP4), through the real service."""
    if scenario.seed_policy is None:
        return
    await context.policy.upsert(project_id, **scenario.seed_policy)


# --------------------------------------------------------------------- drivers


async def _drive_full(
    context: AppContext, task_id: int, obs: Observations, timeout: float,
) -> None:
    """Start the workflow, approve the architecture, run to a terminal state."""
    await context.workflow_manager.start_workflow(task_id)
    reached = await _wait_status(
        context,
        task_id,
        {TaskStatus.AWAITING_ARCHITECTURE_APPROVAL.value} | TERMINAL,
        timeout,
    )
    if reached != TaskStatus.AWAITING_ARCHITECTURE_APPROVAL.value:
        # Triage may legitimately have routed away from implementation, or the
        # architect failed. Either way there is nothing to approve.
        return

    # The one human intervention this driver performs.
    obs.human_interventions += 1
    await context.workflow_manager.resume_approval(task_id, "approve")
    await _wait_status(context, task_id, TERMINAL, timeout)
    await _settle(context, task_id, timeout)


async def _drive_advisory(context: AppContext, task_id: int, timeout: float) -> None:
    """A non-implementation request must finish without an approval gate."""
    await context.workflow_manager.start_workflow(task_id)
    await _wait_status(
        context,
        task_id,
        TERMINAL | {TaskStatus.AWAITING_ARCHITECTURE_APPROVAL.value},
        timeout,
    )
    await _settle(context, task_id, timeout)


async def _settle(context: AppContext, task_id: int, timeout: float) -> None:
    """Wait for the graph itself to finish before measuring anything.

    Reaching a status is not the same as finishing: CHANGES_REQUESTED is
    transient while the Engineer/Reviewer repair loop is still cycling. Measuring
    at the first sight of it counted one engineer run where three had been
    scheduled, so the repair-iteration metric under-reported.
    """
    try:
        await asyncio.wait_for(
            context.workflow_manager.wait_until_idle(task_id), timeout=timeout,
        )
    except asyncio.TimeoutError:
        raise HarnessError(
            f"the workflow graph for task {task_id} was still running after "
            f"{timeout}s; measurements would describe a half-finished workflow"
        ) from None


async def _drive_cancel(
    context: AppContext, task_id: int, obs: Observations, timeout: float,
) -> None:
    await context.workflow_manager.start_workflow(task_id)
    # Wait until an execution is genuinely in flight, so the cancel exercises
    # process teardown rather than a not-yet-started task.
    if not await _wait_for_running_execution(context, task_id, timeout=30.0):
        raise HarnessError("no execution started, so cancellation was not exercised")

    obs.human_interventions += 1
    await context.workflow_manager.cancel(task_id)
    final = await _wait_status(context, task_id, TERMINAL, timeout)
    obs.cancellation_honoured = final == TaskStatus.CANCELLED.value


async def _drive_restart(
    context: AppContext,
    settings: Settings,
    task_id: int,
    obs: Observations,
    timeout: float,
) -> AppContext:
    """Tear SceneWorks down mid-execution and rebuild it on the same database.

    Returns the rebuilt context (the caller observes through it).
    """
    await context.workflow_manager.start_workflow(task_id)
    if not await _wait_for_running_execution(context, task_id, timeout=30.0):
        raise HarnessError("no execution started, so restart was not exercised")

    # Capture pre-restart state so recovery can be described honestly.
    async with context.engine_factory() as session:
        before = await session.get(Task, task_id)
        status_before = before.status
        commit_before = before.result_commit
        worktree_before = before.worktree_path or before.architecture_worktree_path

    # This is the restart: shutdown marks in-flight executions INTERRUPTED.
    await context.shutdown()

    rebuilt = await build_context(settings)
    # The rebuilt registry has a stock fake backend; scripts are irrelevant now
    # because the point is what survived, not what runs next.

    async with rebuilt.engine_factory() as session:
        after = await session.get(Task, task_id)
        rows = (await session.execute(
            __import__("sqlalchemy").select(Execution).where(Execution.task_id == task_id)
        )).scalars().all()
        interrupted = [r.id for r in rows if r.status == "INTERRUPTED"]
        completed = [r.id for r in rows if r.status == "COMPLETED"]
        status_after = after.status
        commit_after = after.result_commit
        worktree_after = after.worktree_path or after.architecture_worktree_path

    obs.recovery = {
        "status_before_restart": status_before,
        "status_after_restart": status_after,
        "interrupted_executions": interrupted,
        "completed_executions_preserved": completed,
        "result_commit_before": commit_before,
        "result_commit_after": commit_after,
        "worktree_before": worktree_before,
        "worktree_after": worktree_after,
        "worktree_still_on_disk": bool(
            worktree_after and Path(worktree_after).is_dir()
        ),
        # What a retry would do, stated rather than implied.
        "retry_behaviour": _retry_behaviour(status_after),
    }
    return rebuilt


def _retry_behaviour(status: str) -> str:
    if status == TaskStatus.FAILED.value:
        return (
            "retry is available: a task with an architecture resumes at "
            "implementation, one without restarts from triage"
        )
    if status == TaskStatus.AWAITING_ARCHITECTURE_APPROVAL.value:
        return "waiting for human approval; the checkpoint is preserved"
    if status in (TaskStatus.READY_TO_IMPLEMENT.value, TaskStatus.CHANGES_REQUESTED.value):
        return "auto-resumed at the engineer node on startup"
    return "no retry path defined for this state"


# ---------------------------------------------------------------- observation


async def _observe(
    context: AppContext,
    scenario: Scenario,
    task_id: int,
    repo_path: Path,
    obs: Observations,
) -> None:
    """Measure the outcome from the database, Git and the event store."""
    async with context.engine_factory() as session:
        task = await session.get(Task, task_id)
        if task is None:
            raise HarnessError(f"task {task_id} vanished during the scenario")
        obs.final_task_status = task.status
        obs.base_commit = task.base_commit or obs.base_commit
        obs.result_commit = task.result_commit
        obs.task_branch = task.task_branch
        obs.architecture_result_present = bool((task.architecture_result or "").strip())
        obs.architecture_result_bytes = len((task.architecture_result or "").encode())
        worktree_path = task.worktree_path

        rows = (await session.execute(
            __import__("sqlalchemy").select(Execution).where(Execution.task_id == task_id)
        )).scalars().all()

    obs.execution_ids = tuple(r.id for r in rows)
    obs.backend_failures = sum(1 for r in rows if r.status == "FAILED")
    obs.engineer_executions = sum(1 for r in rows if r.role == "engineer")
    obs.triage_ran = any(r.role == "triage" for r in rows)
    obs.advisory_roles_executed = frozenset(
        r.role for r in rows if r.role in {"product", "cto", "technical_expert"}
    )

    await _observe_routing(context, task_id, obs)
    await _observe_memory(context, task_id, obs)
    await _observe_policy(context, task_id, obs)
    await _observe_review(rows, obs)
    await _observe_git(context, scenario, task_id, repo_path, worktree_path, obs)

    # A reviewer that approved work whose checks do not pass is a false
    # approval. Measurable only when both facts are known.
    if obs.review_verdicts and obs.tests_pass_at_result is not None:
        approved = obs.review_verdicts[-1] == "APPROVED"
        obs.reviewer_false_approval = approved and not obs.tests_pass_at_result


async def _observe_routing(context: AppContext, task_id: int, obs: Observations) -> None:
    """Recover the triage decision from the events it emitted."""
    import json

    events = await context.event_store.list_for_task(task_id, limit=1000)
    for event in events:
        if event.type == "workflow.triage.completed":
            decision = (event.payload or {}).get("result") or {}
            if isinstance(decision, str):
                try:
                    decision = json.loads(decision)
                except (json.JSONDecodeError, TypeError):
                    decision = {}
            obs.request_type = decision.get("request_type")
            obs.requires_implementation = decision.get("requires_implementation")
            obs.triage_degraded = bool(decision.get("triage_degraded", False))
            obs.advisory_roles_selected = frozenset(
                role
                for role, key in (
                    ("product", "use_product"),
                    ("cto", "use_cto"),
                    ("technical_expert", "use_technical_expert"),
                )
                if decision.get(key)
            )
            break


async def _observe_policy(
    context: AppContext, task_id: int, obs: Observations,
) -> None:
    """Recover detected policy violations from the event they were recorded as.

    This is the WP4 closure evidence: reading policy.violation_detected proves
    SceneWorks' own deterministic check fired, independent of whatever the
    (possibly scripted) Reviewer's verdict text says.
    """
    from app.events import types as event_types

    paths: set[str] = set()
    events = await context.event_store.list_for_task(task_id, limit=1000)
    for event in events:
        if event.type != event_types.POLICY_VIOLATION_DETECTED:
            continue
        for violation in (event.payload or {}).get("violations") or []:
            path = violation.get("path")
            if path:
                paths.add(path)
    obs.policy_violations_detected = frozenset(paths)


async def _observe_memory(
    context: AppContext, task_id: int, obs: Observations,
) -> None:
    """Recover what project memory the workflow injected, from its own events.

    Read from the event log rather than by re-running retrieval: the question is
    what the workflow *actually gave the agent*, not what retrieval would return
    if asked again.
    """
    from app.events import types as event_types

    injected_ids: set[int] = set()
    proposed_ids: set[int] = set()
    terms: tuple[str, ...] = ()

    events = await context.event_store.list_for_task(task_id, limit=1000)
    for event in events:
        if event.type != event_types.MEMORY_INJECTED:
            continue
        payload = event.payload or {}
        injected_ids.update(payload.get("injected_ids") or [])
        proposed_ids.update(payload.get("proposed_not_injected") or [])
        if payload.get("query_terms"):
            terms = tuple(payload["query_terms"])

    obs.memory_query_terms = terms
    obs.memories_injected = frozenset(
        await _memory_titles(context, injected_ids)
    )
    obs.memories_proposed_not_injected = frozenset(
        await _memory_titles(context, proposed_ids)
    )


async def _memory_titles(context: AppContext, ids: set[int]) -> list[str]:
    titles: list[str] = []
    for memory_id in sorted(ids):
        mem = await context.memory.get(memory_id)
        if mem is not None:
            titles.append(mem.title)
    return titles


async def _observe_review(rows, obs: Observations) -> None:
    from app.services.workflow import parse_review_verdict

    verdicts: list[str] = []
    for row in sorted(
        (r for r in rows if r.role == "reviewer"),
        key=lambda r: (r.started_at or r.created_at),
    ):
        if row.status == "COMPLETED":
            verdicts.append(parse_review_verdict(row.result or ""))
    obs.review_verdicts = tuple(verdicts)
    if verdicts:
        obs.reviewer_detected_defect = any(v == "CHANGES_REQUESTED" for v in verdicts)
        # One engineer run is the original implementation; every later one is a
        # repair driven by a review.
        obs.repair_iterations = max(0, obs.engineer_executions - 1)


async def _observe_git(
    context: AppContext,
    scenario: Scenario,
    task_id: int,
    repo_path: Path,
    worktree_path: str | None,
    obs: Observations,
) -> None:
    """Ask Git what changed, and run the repository's checks on the result."""
    if not worktree_path or not Path(worktree_path).is_dir():
        # No implementation worktree: nothing was built. Leave file and result
        # measurements as "not applicable" rather than inventing empties that
        # look like a measured no-change.
        return

    worktree = Path(worktree_path)
    base = obs.base_commit
    if not base:
        return

    try:
        changed = await context.git.changed_files(worktree, base)
        obs.files_changed = frozenset(changed)
        diff = await context.git.diff(worktree, base)
        obs.diff_bytes = len((diff.get("full") or "").encode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise HarnessError(f"could not read the result diff: {exc}") from exc

    expected = scenario.expect_files_changed
    obs.files_expected_and_changed = frozenset(expected & obs.files_changed)
    obs.files_expected_but_unchanged = frozenset(expected - obs.files_changed)
    # "Unexpected" is only meaningful for a scenario that said what it expected.
    if expected:
        obs.files_unexpectedly_changed = frozenset(obs.files_changed - expected)

    # The measurement that makes "implementation correct" real: run the
    # repository's own checks against the committed result.
    result_ok, output = run_check(worktree)
    obs.tests_pass_at_result = result_ok
    obs.test_output_tail = output


# ------------------------------------------------------------------- waiting


async def _wait_status(
    context: AppContext, task_id: int, statuses: set[str], timeout: float,
) -> str:
    deadline = time.monotonic() + timeout
    last = "?"
    while time.monotonic() < deadline:
        async with context.engine_factory() as session:
            task = await session.get(Task, task_id)
            if task is not None:
                last = task.status
                if task.status in statuses:
                    return task.status
        await asyncio.sleep(0.05)
    raise HarnessError(
        f"task {task_id} never reached {sorted(statuses)} within {timeout}s "
        f"(last status: {last})"
    )


async def _wait_for_running_execution(
    context: AppContext, task_id: int, timeout: float,
) -> bool:
    """Wait until at least one execution for this task is actually running."""
    import sqlalchemy

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        async with context.engine_factory() as session:
            rows = (await session.execute(
                sqlalchemy.select(Execution).where(Execution.task_id == task_id)
            )).scalars().all()
        if any(r.status in ("STARTING", "RUNNING") for r in rows):
            return True
        await asyncio.sleep(0.05)
    return False


# ----------------------------------------------------------------- suite entry


async def run_suite(
    scenarios: list[Scenario],
    keep_workdir: bool = False,
    timeout: float = DEFAULT_TIMEOUT,
    progress=None,
    backend: str = "fake",
) -> tuple[list[ScenarioResult], dict]:
    """Run scenarios sequentially. Returns (results, environment notes)."""
    workdir = Path(tempfile.mkdtemp(prefix="sceneworks-qualification-"))
    results: list[ScenarioResult] = []
    environment = {
        "sceneworks_version": __version__,
        "workdir": str(workdir),
        "git_available": shutil.which("git") is not None,
        "backend": backend,
    }
    try:
        for scenario in scenarios:
            if progress:
                progress(scenario)
            results.append(
                await run_scenario(scenario, workdir, timeout=timeout, backend=backend)
            )
    finally:
        if not keep_workdir:
            shutil.rmtree(workdir, ignore_errors=True)
            environment["workdir"] = "(removed)"
    return results, environment


# Kept out of the public surface but handy in tests: build a scenario variant.
def with_timeout(scenario: Scenario, **overrides) -> Scenario:
    return replace(scenario, **overrides)
