"""Paired productivity benchmark runner (WP9).

Each task is resolved to one immutable base commit. SceneWorks and a direct
single Engineer backend run from independent worktrees at that same commit and
are judged by the same repository-owned verification commands and file rules.
"""

from __future__ import annotations

import asyncio
import platform
import shutil
import tempfile
import time
import uuid
from pathlib import Path

from sqlalchemy import select

from app import __version__
from app.agents.base import AgentEventSink, AgentRequest, Workspace
from app.config.settings import Settings
from app.context import AppContext, build_context
from app.domain.task_states import TaskStatus
from app.git.workspace import GitWorktreeService
from app.models import Execution, Project, Task

from benchmarking.models import (
    BenchmarkManifest,
    BenchmarkReport,
    BenchmarkTask,
    CommandResult,
    TrialResult,
)
from benchmarking.scoring import build_comparisons, finalize_trial

_DONEISH = {
    TaskStatus.READY_FOR_HUMAN.value,
    TaskStatus.REJECTED.value,
    TaskStatus.FAILED.value,
    TaskStatus.CANCELLED.value,
    TaskStatus.CHANGES_REQUESTED.value,
}


async def run_manifest(
    manifest: BenchmarkManifest,
    *,
    modes: tuple[str, ...] = ("sceneworks", "direct"),
    keep_workdir: bool = False,
    workdir: Path | None = None,
    progress=None,
) -> BenchmarkReport:
    invalid = set(modes) - {"sceneworks", "direct"}
    if invalid:
        raise ValueError(f"unknown benchmark mode(s): {sorted(invalid)}")

    owned_tmp = None
    if workdir is None:
        owned_tmp = tempfile.TemporaryDirectory(prefix="sceneworks-benchmark-")
        root = Path(owned_tmp.name)
    else:
        root = workdir.resolve()
        root.mkdir(parents=True, exist_ok=True)

    report = BenchmarkReport(
        manifest_name=manifest.name,
        backend=manifest.backend,
        environment={
            "sceneworks_version": __version__,
            "python": platform.python_version(),
            "platform": platform.platform(),
            "workdir": str(root),
        },
    )

    try:
        for task in manifest.tasks:
            task_root = root / task.key
            task_root.mkdir(parents=True, exist_ok=True)
            base_commit, preflight_error, baseline = await _preflight(
                task, manifest.backend, task_root / "preflight"
            )
            for repeat in range(1, manifest.repeats + 1):
                # Alternate order to reduce systematic provider/time-of-run bias.
                order = list(modes)
                if repeat % 2 == 0:
                    order.reverse()
                for mode in order:
                    if progress:
                        progress(task, mode, repeat)
                    if preflight_error:
                        trial = TrialResult(
                            task_key=task.key,
                            mode=mode,
                            repeat=repeat,
                            blocker=preflight_error,
                            resolved_base_commit=base_commit,
                            evidence={"baseline_verification": [r.model_dump() for r in baseline]},
                        )
                    elif mode == "sceneworks":
                        trial = await _run_sceneworks(
                            task,
                            manifest.backend,
                            repeat,
                            base_commit,
                            task_root / f"sceneworks-{repeat}",
                        )
                    else:
                        trial = await _run_direct(
                            task,
                            manifest.backend,
                            repeat,
                            base_commit,
                            task_root / f"direct-{repeat}",
                        )
                    trial.evidence.setdefault(
                        "baseline_verification", [r.model_dump() for r in baseline]
                    )
                    report.trials.append(finalize_trial(trial, task))

        report.comparisons = build_comparisons(report.trials)
        expected = len(manifest.tasks) * manifest.repeats * len(modes)
        return report.finalize(expected)
    finally:
        if owned_tmp is not None:
            if keep_workdir:
                # TemporaryDirectory would otherwise remove it. Copy to a stable
                # sibling and expose the path in report environment.
                source = Path(owned_tmp.name)
                stable = Path(tempfile.mkdtemp(prefix="sceneworks-benchmark-kept-"))
                shutil.copytree(source, stable, dirs_exist_ok=True)
                report.environment["workdir"] = str(stable)
            owned_tmp.cleanup()


async def _preflight(
    task: BenchmarkTask, backend: str, root: Path
) -> tuple[str | None, str | None, list[CommandResult]]:
    repo = task.repository_path.expanduser().resolve()
    settings = _settings(root, backend, task.timeout_seconds)
    git = GitWorktreeService(settings)
    info = await git.repo_info(repo)
    if not info.is_git:
        return None, f"repository is not usable: {info.error or repo}", []
    try:
        base = await git.resolve_base_commit(repo, task.base_ref)
        snapshot = await git.create_snapshot_worktree(repo, base, f"bench-preflight-{task.key}")
    except Exception as exc:  # noqa: BLE001
        return None, f"could not pin benchmark base: {type(exc).__name__}: {exc}", []

    try:
        baseline = await _run_commands(
            snapshot.worktree_path,
            task.verification_commands,
            min(task.timeout_seconds, 900),
        )
    finally:
        await git.remove_worktree(repo, snapshot.worktree_path, None)

    all_pass = bool(baseline) and all(item.passed for item in baseline)
    if task.baseline_expectation == "must_fail" and all_pass:
        return base, (
            "benchmark acceptance commands already pass at the pinned base; "
            "the task cannot prove an implementation improvement"
        ), baseline
    if task.baseline_expectation == "must_pass" and not all_pass:
        return base, (
            "benchmark declares a healthy baseline but its verification commands fail"
        ), baseline
    return base, None, baseline


async def _run_sceneworks(
    task: BenchmarkTask,
    backend: str,
    repeat: int,
    base_commit: str,
    root: Path,
) -> TrialResult:
    trial = TrialResult(
        task_key=task.key,
        mode="sceneworks",
        repeat=repeat,
        resolved_base_commit=base_commit,
    )
    started = time.monotonic()
    context: AppContext | None = None
    repo = task.repository_path.expanduser().resolve()
    cleanup: list[tuple[Path, str | None]] = []
    try:
        context = await build_context(_settings(root, backend, task.timeout_seconds))
        await _require_backend(context, backend)
        project, row = await _seed_task(context, task, repo, base_commit)
        await context.workflow_manager.start_workflow(row.id)

        reached = await _wait_status(
            context,
            row.id,
            {TaskStatus.AWAITING_ARCHITECTURE_APPROVAL.value} | _DONEISH,
            task.timeout_seconds,
        )
        if reached == TaskStatus.AWAITING_ARCHITECTURE_APPROVAL.value:
            # The automated benchmark crosses the same human approval gate a
            # production run requires; count it rather than pretending it is free.
            trial.human_interventions += 1
            await context.workflow_manager.resume_approval(row.id, "approve")

        await asyncio.wait_for(
            context.workflow_manager.wait_until_idle(row.id),
            timeout=task.timeout_seconds,
        )

        async with context.engine_factory() as session:
            row = await session.get(Task, row.id)
            executions = (
                await session.execute(
                    select(Execution).where(Execution.task_id == row.id)
                )
            ).scalars().all()

        trial.final_task_status = row.status
        trial.result_commit = row.result_commit
        trial.agent_executions = len(executions)
        trial.architect_executions = sum(1 for e in executions if e.role == "architect")
        trial.engineer_executions = sum(1 for e in executions if e.role == "engineer")
        trial.reviewer_executions = sum(1 for e in executions if e.role == "reviewer")
        trial.review_iterations = max(0, trial.engineer_executions - 1)
        trial.backend_failures = sum(1 for e in executions if e.status == "FAILED")
        trial.execution_targets = [
            {
                "role": e.role,
                "backend": e.backend,
                "model_profile": e.model_profile,
                "model": e.model_name,
                "status": e.status,
            }
            for e in executions
        ]
        trial.evidence["task_id"] = row.id
        trial.evidence["project_id"] = project.id
        trial.evidence["architecture_present"] = bool((row.architecture_result or "").strip())
        trial.evidence["review_result_present"] = bool((row.review_result or "").strip())

        result_path = Path(row.worktree_path) if row.worktree_path else None
        if result_path and result_path.is_dir():
            trial.files_changed = await context.git.changed_files(result_path, base_commit)
            trial.result_commit = await context.git.head_commit(result_path)
            trial.verification = await _run_commands(
                result_path, task.verification_commands, min(task.timeout_seconds, 900)
            )
        else:
            # No implementation worktree is an engineering outcome, not a harness
            # blocker. The quality gate will fail because no verification ran.
            trial.evidence["no_result_worktree"] = True

        for path, branch in (
            (row.architecture_worktree_path, None),
            (row.review_worktree_path, None),
            (row.worktree_path, row.task_branch),
        ):
            if path:
                cleanup.append((Path(path), branch))
    except asyncio.TimeoutError:
        trial.blocker = f"trial exceeded {task.timeout_seconds}s"
    except Exception as exc:  # noqa: BLE001
        trial.blocker = f"SceneWorks trial error: {type(exc).__name__}: {exc}"
    finally:
        trial.duration_seconds = round(time.monotonic() - started, 3)
        if context is not None:
            for path, branch in cleanup:
                try:
                    await context.git.remove_worktree(repo, path, branch)
                except Exception:
                    pass
            await context.shutdown()
    return trial


async def _run_direct(
    task: BenchmarkTask,
    backend: str,
    repeat: int,
    base_commit: str,
    root: Path,
) -> TrialResult:
    trial = TrialResult(
        task_key=task.key,
        mode="direct",
        repeat=repeat,
        resolved_base_commit=base_commit,
    )
    started = time.monotonic()
    context: AppContext | None = None
    repo = task.repository_path.expanduser().resolve()
    worktree = None
    branch = None
    execution_id = f"benchmark-direct-{uuid.uuid4().hex}"
    try:
        context = await build_context(_settings(root, backend, task.timeout_seconds))
        await _require_backend(context, backend)
        role = context.roles.effective("engineer")
        resolution = context.model_router.resolve(role)

        synthetic_id = uuid.uuid4().int % 1_000_000_000
        branch = f"sw-bench-{task.key[:40]}-{repeat}-{uuid.uuid4().hex[:8]}"
        info = await context.git.create_branch_worktree(
            repo, base_commit, synthetic_id, branch
        )
        worktree = info.worktree_path

        project = Project(
            name=f"benchmark-direct-{task.key}",
            description="Direct-agent baseline for SceneWorks productivity benchmark",
            repository_path=str(repo),
            default_branch=base_commit,
            architecture_context_paths=task.architecture_context_paths,
            test_commands=task.verification_commands,
            build_commands=[],
        )
        task_row = Task(
            project_id=0,
            title=task.title,
            description=task.description,
            status=TaskStatus.READY_TO_IMPLEMENT.value,
            priority="medium",
            base_commit=base_commit,
            engineering_contract=task.engineering_contract,
        )
        workspace_dict = {
            "cwd": str(worktree),
            "repo_path": str(repo),
            "branch": branch,
            "base_commit": base_commit,
            "permissions": sorted(p.value for p in role.permissions),
        }
        prompt = await context.prompt_builder.build(
            role=role,
            project=project,
            task=task_row,
            workspace=workspace_dict,
            context_worktree_path=str(worktree),
        )
        request = AgentRequest(
            execution_id=execution_id,
            role="engineer",
            system_prompt=prompt.system,
            user_prompt=prompt.user,
            model_profile=resolution.profile,
            model=resolution.model,
            metadata={"benchmark": True, "task_key": task.key},
        )
        events: list[dict] = []

        async def emit(event_type, payload, severity="info", *, execution_id=None):
            events.append({"type": event_type, "payload": payload, "severity": severity})

        sink = AgentEventSink(execution_id, None, emit)
        workspace = Workspace(
            path=worktree,
            repo_path=repo,
            branch=branch,
            base_commit=base_commit,
            permissions=tuple(workspace_dict["permissions"]),
        )
        provider = context.backends.get(resolution.backend)
        result = await asyncio.wait_for(
            provider.run(request, workspace, sink), timeout=task.timeout_seconds
        )
        trial.agent_executions = 1
        trial.engineer_executions = 1
        trial.backend_failures = 1 if result.status == "failed" else 0
        trial.execution_targets = [
            {
                "role": "engineer",
                "backend": resolution.backend,
                "model_profile": resolution.profile,
                "model": resolution.model,
                "status": result.status,
            }
        ]
        trial.evidence["backend_summary"] = result.summary
        trial.evidence["backend_error"] = result.error
        trial.evidence["event_count"] = len(events)

        status = await context.git.status(worktree)
        head = await context.git.head_commit(worktree)
        if status.strip():
            head = await context.git.commit_all(
                worktree, f"benchmark direct result: {task.key}"
            )
        trial.result_commit = head
        trial.files_changed = await context.git.changed_files(worktree, base_commit)
        trial.verification = await _run_commands(
            worktree, task.verification_commands, min(task.timeout_seconds, 900)
        )
    except asyncio.TimeoutError:
        trial.blocker = f"direct trial exceeded {task.timeout_seconds}s"
        if context is not None:
            try:
                await context.backends.get(backend).cancel(execution_id)
            except Exception:
                pass
    except Exception as exc:  # noqa: BLE001
        trial.blocker = f"direct trial error: {type(exc).__name__}: {exc}"
    finally:
        trial.duration_seconds = round(time.monotonic() - started, 3)
        if context is not None:
            if worktree is not None:
                try:
                    await context.git.remove_worktree(repo, worktree, branch)
                except Exception:
                    pass
            await context.shutdown()
    return trial


async def _seed_task(
    context: AppContext,
    spec: BenchmarkTask,
    repo: Path,
    base_commit: str,
) -> tuple[Project, Task]:
    async with context.engine_factory() as session:
        project = Project(
            name=f"benchmark-{spec.key}",
            description="SceneWorks productivity benchmark task",
            repository_path=str(repo),
            # GitWorktreeService accepts any resolvable ref; a commit pins the run.
            default_branch=base_commit,
            architecture_context_paths=spec.architecture_context_paths,
            test_commands=spec.verification_commands,
            build_commands=[],
        )
        session.add(project)
        await session.commit()
        await session.refresh(project)
        row = Task(
            project_id=project.id,
            title=spec.title,
            description=spec.description,
            status=TaskStatus.NEW.value,
            priority="medium",
            engineering_contract=spec.engineering_contract,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return project, row


async def _require_backend(context: AppContext, requested: str) -> None:
    if requested == "fake":
        return
    # The default backend may be overridden per profile by WP8. Validate every
    # registered target lazily during execution; here at least prove the requested
    # benchmark provider is usable instead of recording a BLOCKED run as a loss.
    health = await context.backends.get(requested).health()
    if not health.available:
        raise RuntimeError(f"backend {requested!r} unavailable: {health.detail}")


async def _wait_status(
    context: AppContext, task_id: int, statuses: set[str], timeout: float
) -> str:
    deadline = time.monotonic() + timeout
    last = "?"
    while time.monotonic() < deadline:
        async with context.engine_factory() as session:
            row = await session.get(Task, task_id)
            if row is not None:
                last = row.status
                if last in statuses:
                    return last
        await asyncio.sleep(0.1)
    raise asyncio.TimeoutError(
        f"task {task_id} never reached {sorted(statuses)}; last status={last}"
    )


async def _run_commands(
    cwd: Path, commands: list[str], timeout_seconds: int
) -> list[CommandResult]:
    results: list[CommandResult] = []
    for command in commands:
        started = time.monotonic()
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        timed_out = False
        try:
            output, _ = await asyncio.wait_for(
                proc.communicate(), timeout=timeout_seconds
            )
        except asyncio.TimeoutError:
            timed_out = True
            proc.kill()
            output, _ = await proc.communicate()
        text = output.decode("utf-8", errors="replace")
        results.append(
            CommandResult(
                command=command,
                passed=(not timed_out and proc.returncode == 0),
                returncode=proc.returncode,
                duration_seconds=round(time.monotonic() - started, 3),
                output_tail=text[-6000:],
                timed_out=timed_out,
            )
        )
    return results


def _settings(root: Path, backend: str, execution_timeout: int) -> Settings:
    root.mkdir(parents=True, exist_ok=True)
    return Settings(
        database_url=f"sqlite+aiosqlite:///{(root / 'benchmark.db').as_posix()}",
        worktree_root=root / "worktrees",
        checkpoint_db_path=str(root / "checkpoints.db"),
        default_backend=backend,
        execution_timeout_seconds=execution_timeout,
        git_timeout_seconds=min(max(execution_timeout, 300), 3600),
        log_level="ERROR",
    )
