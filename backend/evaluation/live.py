"""Optional live qualification against a real repository.

This is **not** part of the automated suite. The automated suite must never
depend on a private repository or on a live model, so live mode is opt-in
(``--live PATH``) and reports its own verdict.

What live mode does differently:

- uses the configured agent backend (Gemini ACP by default), not the scripted one;
- runs against a real repository pinned to a real commit;
- cannot assert implementation correctness, because it has no reference
  expectations — it records what happened for human assessment instead.

Safety invariants, enforced here rather than assumed:

- the human working tree is never used as an agent working directory: every
  role gets a commit-pinned worktree under SceneWorks' own worktree root;
- the repository is left on the commit it was on — SceneWorks does not check
  anything out in the user's clone, does not merge and does not push;
- qualification refuses to run if the target repository has uncommitted
  changes that could make the pinned commit unrepresentative.
"""

from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path

from app import __version__
from app.domain.task_states import TaskStatus
from app.models import Project, Task

from evaluation.outcomes import (
    Check,
    Observations,
    QualificationReport,
    ScenarioResult,
    UNSUPPORTED_METRICS,
)

logger = logging.getLogger("sceneworks.qualification.live")

LIVE_KEY = "live-qualification"


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


async def run_live(
    repo_path: Path,
    commit: str | None = None,
    timeout: float = 1800.0,
) -> QualificationReport:
    """Drive one real task against a real repository and record what happened."""
    from app.context import build_context
    from app.config.settings import get_settings

    result = ScenarioResult(
        key=LIVE_KEY,
        title=f"Live qualification against {repo_path.name}",
    )
    result.unsupported = sorted(
        set(UNSUPPORTED_METRICS)
        | {
            "implementation_correct",
            "tests_pass_at_result",
        }
    )
    obs = result.observations
    started = time.monotonic()

    repo = repo_path.expanduser().resolve()
    context = None
    try:
        if not (repo / ".git").exists():
            raise RuntimeError(f"{repo} is not a Git repository")

        dirty = _git(repo, "status", "--porcelain").strip()
        if dirty:
            raise RuntimeError(
                f"{repo} has uncommitted changes. Live qualification pins a "
                "commit, so an unclean tree would make the pinned snapshot "
                "unrepresentative of what you are qualifying. Commit or stash "
                "first. (SceneWorks will not touch your working tree either way.)"
            )

        head_before = _git(repo, "rev-parse", "HEAD").strip()
        base = commit or head_before
        base = _git(repo, "rev-parse", base).strip()
        obs.base_commit = base
        obs.repository_path = str(repo)

        settings = get_settings()
        context = await build_context(settings)
        obs_backend = settings.default_backend

        async with context.engine_factory() as session:
            project = Project(
                name=f"live-qualification-{repo.name}",
                description="Live qualification target (created by python -m evaluation --live)",
                repository_path=str(repo),
                default_branch=_current_branch(repo),
            )
            session.add(project)
            await session.commit()
            await session.refresh(project)

            task = Task(
                project_id=project.id,
                title="Live qualification: describe the repository architecture",
                description=(
                    "Read-only qualification task. Summarise the top-level "
                    "architecture of this repository: the main modules, the "
                    "dependency direction between them, and any boundary that "
                    "looks violated. Do not modify any file."
                ),
                status=TaskStatus.NEW.value,
                base_commit=base,
            )
            session.add(task)
            await session.commit()
            await session.refresh(task)

        obs.project_id, obs.task_id = project.id, task.id

        from evaluation.harness import _observe, _wait_status, TERMINAL

        await context.workflow_manager.start_workflow(task.id)
        await _wait_status(
            context,
            task.id,
            TERMINAL | {TaskStatus.AWAITING_ARCHITECTURE_APPROVAL.value},
            timeout,
        )

        # A read-only architecture task needs no approval; if it is waiting,
        # leave it waiting — a live run must not auto-approve on the human's
        # behalf, because human approval is the invariant being demonstrated.
        from evaluation.scenarios import Scenario

        probe = Scenario(
            key=LIVE_KEY, title=result.title, description="", repo="(live)",
        )
        await _observe(context, probe, task.id, repo, obs)

        head_after = _git(repo, "rev-parse", "HEAD").strip()

        result.checks = _live_checks(obs, head_before, head_after, repo)

    except Exception as exc:  # noqa: BLE001
        logger.exception("live qualification failed")
        result.blockers.append(f"{type(exc).__name__}: {exc}")
        obs_backend = "unknown"
    finally:
        obs.duration_seconds = round(time.monotonic() - started, 3)
        if context is not None:
            try:
                await context.shutdown()
            except Exception:  # noqa: BLE001
                pass

    result.finalize()

    report = QualificationReport(
        sceneworks_version=__version__,
        backend=f"{obs_backend} (live)",
        mode="live",
        results=[result],
        # Live mode qualifies nothing on its own: the automated suite owns the
        # required-scenario contract. Declaring no requirements here keeps a
        # live PASS from being mistaken for a release gate.
        required_scenarios=[],
        environment={"live_repository": str(repo_path)},
    )
    report.duration_seconds = obs.duration_seconds
    return report


def _current_branch(repo: Path) -> str:
    try:
        return _git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
    except RuntimeError:
        return ""


def _live_checks(
    obs: Observations, head_before: str, head_after: str, repo: Path,
) -> list[Check]:
    """What live mode *can* assert: safety and provenance, never correctness."""
    return [
        Check(
            name="safety.working_tree_untouched",
            passed=head_before == head_after,
            expected=head_before,
            actual=head_after,
            detail="SceneWorks must leave the human clone on the same commit",
        ),
        Check(
            name="safety.working_tree_clean",
            passed=not _git(repo, "status", "--porcelain").strip(),
            expected="clean",
            actual=_git(repo, "status", "--porcelain").strip()[:200] or "clean",
            detail="no agent may write into the human working tree",
        ),
        Check(
            name="provenance.commit_pinned",
            passed=bool(obs.base_commit),
            expected="a recorded base commit",
            actual=obs.base_commit,
            detail="analysis must be attributable to one snapshot",
        ),
        Check(
            name="process.reached_a_defined_state",
            passed=obs.final_task_status is not None,
            expected="a defined task status",
            actual=obs.final_task_status,
        ),
        Check(
            name="architecture.present",
            passed=bool(obs.architecture_result_present),
            expected=True,
            actual=obs.architecture_result_present,
            detail=(
                f"{obs.architecture_result_bytes} bytes recorded for human "
                "assessment; usefulness is not scored"
            ),
        ),
    ]
