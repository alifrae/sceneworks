"""Task-workflow integration coverage for WP8 model routing."""

from __future__ import annotations

from app.config.settings import ModelProfileRoute
from app.models import Project, Task


async def test_task_workflow_creation_persists_resolved_model(context, git_repo):
    """The LangGraph path must resolve profiles exactly like manual asks do."""
    context.settings.model_profile_routes = {
        "strongest": ModelProfileRoute(backend="fake", model="workflow-model")
    }

    async with context.engine_factory() as session:
        project = Project(
            name="wp8-routing",
            repository_path=str(git_repo),
            default_branch="main",
        )
        session.add(project)
        await session.commit()
        await session.refresh(project)

        task = Task(
            project_id=project.id,
            title="verify workflow routing",
            description="verify model routing",
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)

    execution = await context.workflow_manager._create_execution(  # noqa: SLF001
        task=task,
        role=context.roles.effective("architect"),
        workspace={"repo_path": str(git_repo)},
        system_prompt="system",
        user_prompt="user",
    )

    assert execution.model_profile == "strongest"
    assert execution.backend == "fake"
    assert execution.model_name == "workflow-model"

    # Prove the selection was persisted, not merely returned transiently.
    async with context.engine_factory() as session:
        persisted = await session.get(type(execution), execution.id)
        assert persisted is not None
        assert persisted.backend == "fake"
        assert persisted.model_name == "workflow-model"
