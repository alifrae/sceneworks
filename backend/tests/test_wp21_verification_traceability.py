from __future__ import annotations

from app.models import Project, Task


async def _seed_project_task(context, git_repo, *, issue: bool = False):
    async with context.engine_factory() as session:
        project = Project(
            name="wp21",
            repository_path=str(git_repo),
            default_branch="main",
            engineering_policy={
                "protected_paths": ["generated/*"],
                "go_no_go_commands": [],
            },
        )
        session.add(project)
        await session.flush()
        task = Task(
            project_id=project.id,
            title="verify me",
            status="READY_FOR_HUMAN",
            work_item_type="bug" if issue else "task",
            requested_mode="change",
            resolved_mode="change",
            base_commit="a" * 40,
            result_commit="b" * 40,
            changed_files=["src/fix.py"],
            engineering_contract={
                "allowed_scope": ["src/*"],
                "required_tests": ["python -m pytest tests/test_fix.py"],
                "acceptance_criteria": ["Regression is fixed", "Runtime remains stable"],
            },
            implementation_summary=(
                "## Implementation summary\nFixed the defect.\n\n"
                "## Resolution record\n\n"
                "### Root cause\nA stale generation token survived seek.\n\n"
                "### Change made\nReset the token when seek invalidates queued frames.\n\n"
                "### Validation performed\nRan the focused regression.\n\n"
                "### Remaining risk\nLong recordings were not exercised."
            ),
            review_result=(
                "## Verdict\nAPPROVED\n\n"
                "## Regression risk\nLong-duration playback remains unverified."
            ),
        )
        session.add(task)
        await session.commit()
        await session.refresh(project)
        await session.refresh(task)
        return project, task


async def test_verification_uses_explicit_evidence_and_does_not_promote_unmapped_criteria(
    context, git_repo
):
    project, task = await _seed_project_task(context, git_repo)
    engineering = await context.engineering_sessions.create(
        project.id,
        task_id=task.id,
        permissions=["repository_read", "shell_execute"],
    )
    await context.engineering_evidence.record(
        engineering.id,
        task_id=task.id,
        category="command",
        operation="command.run",
        status="COMPLETED",
        payload={
            "command": "python",
            "arguments": ["-m", "pytest", "tests/test_fix.py"],
            "exit_code": 0,
            "criterion_ids": ["AC1"],
        },
    )

    result = await context.verification.synthesize(task.id)

    assert result["acceptance_criteria"][0]["status"] == "PASS"
    assert result["acceptance_criteria"][1]["status"] == "UNVERIFIABLE"
    assert result["required_tests"][0]["status"] == "PASS"
    assert result["scope"][0]["status"] == "PASS"
    assert result["scope"][1]["status"] == "PASS"
    assert result["reviewer"]["status"] == "PASS"
    assert result["overall"] == "UNVERIFIABLE"


async def test_objective_failure_overrides_reviewer_approval(context, git_repo):
    project, task = await _seed_project_task(context, git_repo)
    engineering = await context.engineering_sessions.create(
        project.id,
        task_id=task.id,
        permissions=["repository_read", "shell_execute"],
    )
    await context.engineering_evidence.record(
        engineering.id,
        task_id=task.id,
        category="command",
        operation="command.run",
        status="COMPLETED",
        payload={
            "command": "python",
            "arguments": ["-m", "pytest", "tests/test_fix.py"],
            "exit_code": 1,
            "criterion_ids": ["AC1"],
        },
    )

    result = await context.verification.synthesize(task.id)

    assert result["acceptance_criteria"][0]["status"] == "FAIL"
    assert result["required_tests"][0]["status"] == "FAIL"
    assert result["reviewer"]["status"] == "PASS"
    assert result["overall"] == "FAIL"


async def test_issue_acceptance_appends_immutable_resolution_snapshot(context, git_repo):
    _project, task = await _seed_project_task(context, git_repo, issue=True)

    await context.workflow_manager.accept(task.id)
    first = await context.verification.resolution(task.id)
    second = await context.verification.capture_resolution(task.id)

    assert first is not None
    assert second == first
    assert first["root_cause"]["text"] == "A stale generation token survived seek."
    assert first["root_cause"]["authority"] == "engineer_claim"
    assert first["change_made"]["authority"] == "engineer_claim"
    assert first["resolved_commit"] == "b" * 40
    assert first["changed_files"] == ["src/fix.py"]
    assert first["remaining_risk"]["authority"] == "reviewer_claim"
    assert first["verification"]["overall"] == "UNVERIFIABLE"


async def test_role_profile_override_is_editable_and_resolved(client):
    response = await client.patch(
        "/api/settings",
        json={
            "model_profile_routes": {
                "research": {"backend": "fake", "model": "research-model"}
            }
        },
    )
    assert response.status_code == 200

    response = await client.patch(
        "/api/settings/routing",
        json={"role_profile_overrides": {"engineer": "research"}},
    )
    assert response.status_code == 200
    body = response.json()
    engineer = next(row for row in body["roles"] if row["key"] == "engineer")
    assert engineer["default_profile"] == "coding"
    assert engineer["effective_profile"] == "research"
    assert engineer["profile_source"] == "role_override"
    assert engineer["resolved_backend"] == "fake"
    assert engineer["resolved_model"] == "research-model"

    response = await client.patch(
        "/api/settings/routing",
        json={"role_profile_overrides": {}},
    )
    assert response.status_code == 200
    engineer = next(row for row in response.json()["roles"] if row["key"] == "engineer")
    assert engineer["effective_profile"] == "coding"
    assert engineer["profile_source"] == "role_default"
