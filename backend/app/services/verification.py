"""Task-level objective verification and immutable issue-resolution snapshots.

WP21 deliberately projects existing SceneWorks evidence instead of introducing a
second verification ledger.  Free-form engineering claims stay claims; only
mechanically attributable observations can produce PASS/FAIL.
"""

from __future__ import annotations

import fnmatch
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.engineering_models import EngineeringEvidence
from app.events.store import EventStore
from app.models import Event, Execution, Project, Task
from app.services.policy_check import check_protected_paths

PASS = "PASS"
FAIL = "FAIL"
UNVERIFIABLE = "UNVERIFIABLE"
NOT_APPLICABLE = "NOT_APPLICABLE"
_FAILURE_STATUSES = {"FAILED", "ERROR", "CRASHED", "CANCELLED", "INTERRUPTED", "LOST"}
_ISSUE_TYPES = {"bug", "feature", "idea"}


class TaskVerificationError(RuntimeError):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm_command(value: str) -> str:
    return " ".join(str(value or "").strip().split())


def _evidence_ref(row: EngineeringEvidence, detail: str | None = None) -> dict[str, Any]:
    return {
        "source": "engineering_evidence",
        "id": str(row.id),
        "label": detail or f"{row.category}:{row.operation}",
        "status": row.status,
    }


def _task_ref(label: str, status: str | None = None) -> dict[str, Any]:
    return {"source": "task", "id": None, "label": label, "status": status}


def _git_ref(label: str, status: str | None = None) -> dict[str, Any]:
    return {"source": "git", "id": None, "label": label, "status": status}


def _check(key: str, label: str, status: str, detail: str = "", evidence: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "status": status,
        "detail": detail,
        "evidence": evidence or [],
    }


def _criterion_ids(payload: dict[str, Any]) -> set[str]:
    raw: list[Any] = []
    if payload.get("criterion_id") is not None:
        raw.append(payload.get("criterion_id"))
    many = payload.get("criterion_ids")
    if isinstance(many, list):
        raw.extend(many)
    result: set[str] = set()
    for item in raw:
        value = str(item or "").strip().upper().replace(" ", "")
        if not value:
            continue
        if value.isdigit():
            value = f"AC{value}"
        result.add(value)
    return result


def _objective_result(row: EngineeringEvidence) -> str | None:
    """Return PASS/FAIL only when the evidence itself has deterministic semantics."""
    payload = dict(row.payload or {})
    if row.status.upper() in _FAILURE_STATUSES:
        return FAIL
    if row.category == "command":
        exit_code = payload.get("exit_code")
        if isinstance(exit_code, int):
            return PASS if exit_code == 0 else FAIL
        return None
    if row.category == "verification" and isinstance(payload.get("passed"), bool):
        return PASS if payload["passed"] else FAIL
    # A completed screenshot/visual-diff observation is evidence, but without a
    # criterion-specific threshold it does not itself prove a requirement.
    return None


def _command_text(row: EngineeringEvidence) -> str:
    payload = dict(row.payload or {})
    command = str(payload.get("command") or "").strip()
    arguments = payload.get("arguments")
    if not isinstance(arguments, list):
        arguments = payload.get("args") if isinstance(payload.get("args"), list) else []
    return _norm_command(" ".join([command, *[str(item) for item in arguments]]))


def _latest_command(rows: Iterable[EngineeringEvidence], configured: str) -> EngineeringEvidence | None:
    wanted = _norm_command(configured)
    matches = [row for row in rows if row.category == "command" and _command_text(row) == wanted]
    return matches[-1] if matches else None


def _path_matches_scope(path: str, raw_scope: str) -> bool:
    path = path.strip().replace("\\", "/")
    scope = raw_scope.strip().replace("\\", "/").lstrip("./")
    if not scope:
        return False
    if any(char in scope for char in "*?["):
        return fnmatch.fnmatchcase(path, scope)
    base = scope.rstrip("/")
    return path == base or path.startswith(base + "/")


def _changed_file_provenance_missing(task: Task) -> bool:
    return bool(task.result_commit and task.base_commit and task.result_commit != task.base_commit and not (task.changed_files or []))


def _review_verdict(text: str | None) -> str | None:
    upper = (text or "").upper()
    if "CHANGES_REQUESTED" in upper or "CHANGES REQUESTED" in upper:
        return "CHANGES_REQUESTED"
    if "APPROVED" in upper:
        return "APPROVED"
    return None


def _markdown_section(text: str | None, heading: str) -> str | None:
    if not text:
        return None
    escaped = re.escape(heading)
    pattern = re.compile(
        rf"(?ims)^\s*(?:#{{1,6}}\s+{escaped}\s*|\*\*{escaped}\*\*\s*:?)\s*$\n?(.*?)(?=^\s*(?:#{{1,6}}\s+|\*\*[^\n]+\*\*\s*:?)|\Z)"
    )
    match = pattern.search(text)
    if not match:
        return None
    value = match.group(1).strip()
    return value or None


class TaskVerificationService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        event_store: EventStore,
    ) -> None:
        self._session_factory = session_factory
        self._events = event_store

    async def synthesize(self, task_id: int) -> dict[str, Any]:
        async with self._session_factory() as session:
            task = await session.get(Task, task_id)
            if task is None:
                raise TaskVerificationError(f"task {task_id} not found")
            project = await session.get(Project, task.project_id)
            if project is None:
                raise TaskVerificationError(f"project {task.project_id} not found")
            evidence_rows = list(
                (
                    await session.execute(
                        select(EngineeringEvidence)
                        .where(EngineeringEvidence.task_id == task_id)
                        .order_by(EngineeringEvidence.id.asc())
                    )
                ).scalars().all()
            )

        contract = dict(task.engineering_contract or {})
        policy = dict(project.engineering_policy or {})
        changed_files = [str(path).replace("\\", "/") for path in (task.changed_files or [])]
        provenance_missing = _changed_file_provenance_missing(task)

        criteria: list[dict[str, Any]] = []
        for index, criterion in enumerate(contract.get("acceptance_criteria") or [], start=1):
            criterion_id = f"AC{index}"
            mapped = [row for row in evidence_rows if criterion_id in _criterion_ids(dict(row.payload or {}))]
            outcomes = [_objective_result(row) for row in mapped]
            if FAIL in outcomes:
                status = FAIL
                detail = "Explicitly mapped objective evidence failed."
            elif PASS in outcomes:
                status = PASS
                detail = "Explicitly mapped objective evidence passed."
            elif mapped:
                status = UNVERIFIABLE
                detail = "Evidence is explicitly mapped, but it has no deterministic pass/fail semantics for this criterion."
            else:
                status = UNVERIFIABLE
                detail = "No explicit objective evidence mapping was captured for this criterion."
            criteria.append(
                _check(
                    criterion_id,
                    str(criterion),
                    status,
                    detail,
                    [_evidence_ref(row) for row in mapped],
                )
            )

        required_tests: list[dict[str, Any]] = []
        for index, command in enumerate(contract.get("required_tests") or [], start=1):
            row = _latest_command(evidence_rows, str(command))
            if row is None:
                required_tests.append(
                    _check(
                        f"TEST{index}",
                        str(command),
                        UNVERIFIABLE,
                        "No attributable SceneWorks command evidence matches this required test exactly.",
                    )
                )
                continue
            outcome = _objective_result(row)
            status = outcome or UNVERIFIABLE
            exit_code = (row.payload or {}).get("exit_code")
            required_tests.append(
                _check(
                    f"TEST{index}",
                    str(command),
                    status,
                    f"Observed exact command with exit code {exit_code!r}.",
                    [_evidence_ref(row, f"command evidence #{row.id}")],
                )
            )

        scope_checks: list[dict[str, Any]] = []
        allowed_scope = [str(item) for item in (contract.get("allowed_scope") or []) if str(item).strip()]
        if allowed_scope:
            if provenance_missing:
                scope_checks.append(
                    _check(
                        "ALLOWED_SCOPE",
                        "Allowed scope",
                        UNVERIFIABLE,
                        "The task has a non-base result commit but no captured changed-file provenance.",
                    )
                )
            else:
                outside = [path for path in changed_files if not any(_path_matches_scope(path, scope) for scope in allowed_scope)]
                status = FAIL if outside else PASS
                detail = (
                    "Changed paths outside allowed scope: " + ", ".join(outside)
                    if outside
                    else "All captured changed paths are within the configured allowed scope."
                )
                scope_checks.append(
                    _check(
                        "ALLOWED_SCOPE",
                        "Allowed scope",
                        status,
                        detail,
                        [_git_ref(f"{len(changed_files)} captured changed file(s)", status)],
                    )
                )
        else:
            scope_checks.append(_check("ALLOWED_SCOPE", "Allowed scope", NOT_APPLICABLE, "No allowed_scope constraint is configured."))

        protected = [str(item) for item in (policy.get("protected_paths") or []) if str(item).strip()]
        if protected:
            if provenance_missing:
                scope_checks.append(
                    _check(
                        "PROTECTED_PATHS",
                        "Protected paths",
                        UNVERIFIABLE,
                        "Changed-file provenance is missing, so protected-path compliance cannot be established.",
                    )
                )
            else:
                violations = check_protected_paths(protected, changed_files)
                status = FAIL if violations else PASS
                detail = (
                    "; ".join(f"{item.path} matches {item.pattern}" for item in violations)
                    if violations
                    else "No captured changed path matches project protected_paths."
                )
                scope_checks.append(
                    _check(
                        "PROTECTED_PATHS",
                        "Protected paths",
                        status,
                        detail,
                        [_git_ref(f"{len(changed_files)} captured changed file(s)", status)],
                    )
                )
        else:
            scope_checks.append(_check("PROTECTED_PATHS", "Protected paths", NOT_APPLICABLE, "No protected_paths policy is configured."))

        policy_checks: list[dict[str, Any]] = []
        for index, command in enumerate(policy.get("go_no_go_commands") or [], start=1):
            row = _latest_command(evidence_rows, str(command))
            if row is None:
                policy_checks.append(
                    _check(
                        f"GONOGO{index}",
                        str(command),
                        UNVERIFIABLE,
                        "No attributable SceneWorks command evidence matches this go/no-go command exactly.",
                    )
                )
            else:
                policy_checks.append(
                    _check(
                        f"GONOGO{index}",
                        str(command),
                        _objective_result(row) or UNVERIFIABLE,
                        f"Observed exact go/no-go command with exit code {(row.payload or {}).get('exit_code')!r}.",
                        [_evidence_ref(row)],
                    )
                )

        semantic_fields = (
            ("architecture_invariants", "Architecture invariant"),
            ("forbidden_dependency_directions", "Dependency direction"),
            ("documentation_requirements", "Documentation requirement"),
            ("performance_constraints", "Performance constraint"),
            ("required_review_checks", "Required review check"),
            ("release_requirements", "Release requirement"),
        )
        for field, label in semantic_fields:
            for index, clause in enumerate(policy.get(field) or [], start=1):
                policy_checks.append(
                    _check(
                        f"{field.upper()}_{index}",
                        f"{label}: {clause}",
                        UNVERIFIABLE,
                        "No dedicated deterministic verifier is registered for this semantic policy clause.",
                    )
                )

        implementation_expected = bool(
            task.result_commit
            or task.implementation_summary
            or (task.resolved_mode or task.requested_mode) == "change"
        )
        verdict = _review_verdict(task.review_result)
        if not implementation_expected:
            reviewer = _check("REVIEW", "Independent reviewer", NOT_APPLICABLE, "No implementation review is required for this work item.")
        elif verdict == "APPROVED":
            reviewer = _check("REVIEW", "Independent reviewer", PASS, "Reviewer returned APPROVED. This is a review boundary, not proof of other checks.", [_task_ref("Reviewer verdict: APPROVED", PASS)])
        elif verdict == "CHANGES_REQUESTED":
            reviewer = _check("REVIEW", "Independent reviewer", FAIL, "Reviewer requested changes.", [_task_ref("Reviewer verdict: CHANGES_REQUESTED", FAIL)])
        else:
            reviewer = _check("REVIEW", "Independent reviewer", UNVERIFIABLE, "No final structured Reviewer verdict is available.")

        material = [*criteria, *required_tests, *scope_checks, *policy_checks]
        material_required = [item for item in material if item["status"] != NOT_APPLICABLE]
        reviewer_required = reviewer["status"] != NOT_APPLICABLE
        if any(item["status"] == FAIL for item in material_required) or reviewer["status"] == FAIL:
            overall = FAIL
        elif not material_required:
            overall = UNVERIFIABLE
        elif any(item["status"] == UNVERIFIABLE for item in material_required) or (reviewer_required and reviewer["status"] != PASS):
            overall = UNVERIFIABLE
        else:
            overall = PASS

        counts = Counter(item["status"] for item in [*material_required, reviewer] if item["status"] != NOT_APPLICABLE)
        return {
            "task_id": task.id,
            "project_id": task.project_id,
            "task_status": task.status,
            "overall": overall,
            "acceptance_criteria": criteria,
            "required_tests": required_tests,
            "scope": scope_checks,
            "policy": policy_checks,
            "reviewer": reviewer,
            "summary": {
                "pass": counts[PASS],
                "fail": counts[FAIL],
                "unverifiable": counts[UNVERIFIABLE],
                "not_applicable": sum(
                    1 for item in [*material, reviewer] if item["status"] == NOT_APPLICABLE
                ),
            },
            "evidence_count": len(evidence_rows),
            "base_commit": task.base_commit,
            "result_commit": task.result_commit,
            "changed_files": changed_files,
            "authority_note": (
                "PASS/FAIL is produced only from deterministic SceneWorks observations or the independent review boundary. "
                "Reviewer/Engineer prose never promotes an unverifiable criterion."
            ),
        }

    async def resolution(self, task_id: int) -> dict[str, Any] | None:
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(Event)
                    .where(Event.task_id == task_id, Event.type == "task.resolution")
                    .order_by(Event.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            return dict(row.payload or {}) if row is not None else None

    async def view(self, task_id: int) -> dict[str, Any]:
        return {
            "verification": await self.synthesize(task_id),
            "resolution": await self.resolution(task_id),
        }

    async def capture_resolution(self, task_id: int) -> dict[str, Any] | None:
        existing = await self.resolution(task_id)
        if existing is not None:
            return existing

        verification = await self.synthesize(task_id)
        async with self._session_factory() as session:
            task = await session.get(Task, task_id)
            if task is None:
                raise TaskVerificationError(f"task {task_id} not found")
            if task.work_item_type not in _ISSUE_TYPES or task.status != "ACCEPTED":
                return None
            executions = list(
                (
                    await session.execute(
                        select(Execution)
                        .where(Execution.task_id == task_id)
                        .order_by(Execution.created_at.desc())
                    )
                ).scalars().all()
            )

        engineer = next((row for row in executions if row.role == "engineer"), None)
        reviewer = next((row for row in executions if row.role == "reviewer"), None)
        implementation = task.implementation_summary or (engineer.result if engineer else None) or ""
        review = task.review_result or (reviewer.result if reviewer else None) or ""

        root_cause = _markdown_section(implementation, "Root cause")
        change_made = _markdown_section(implementation, "Change made")
        if not change_made:
            change_made = _markdown_section(implementation, "Implementation summary")
        remaining_risk = _markdown_section(review, "Regression risk") or _markdown_section(implementation, "Remaining risk")

        payload = {
            "schema_version": 1,
            "captured_at": _now_iso(),
            "task_id": task.id,
            "work_item_type": task.work_item_type,
            "root_cause": (
                {
                    "text": root_cause,
                    "authority": "engineer_claim",
                    "source_execution_id": engineer.id if engineer else None,
                }
                if root_cause
                else None
            ),
            "change_made": (
                {
                    "text": change_made,
                    "authority": "engineer_claim",
                    "source_execution_id": engineer.id if engineer else None,
                }
                if change_made
                else None
            ),
            "resolved_commit": task.result_commit,
            "changed_files": list(task.changed_files or []),
            "verification": verification,
            "remaining_risk": (
                {
                    "text": remaining_risk,
                    "authority": "reviewer_claim" if _markdown_section(review, "Regression risk") else "engineer_claim",
                    "source_execution_id": reviewer.id if _markdown_section(review, "Regression risk") and reviewer else (engineer.id if engineer else None),
                }
                if remaining_risk
                else None
            ),
            "authority_note": (
                "resolved_commit/changed_files and verification are SceneWorks-derived. "
                "Root cause/change/risk prose is retained as an attributed engineering claim."
            ),
        }
        await self._events.append(
            execution_id=None,
            task_id=task_id,
            type="task.resolution",
            payload=payload,
        )
        return payload
