"""Application composition root.

Builds every service once at startup, wires the execution engine's
continuation hook to the LangGraph WorkflowManager, and reconciles
interrupted executions. API handlers access services through this context.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.agents.registry import BackendRegistry
from app.config.settings import Settings, get_settings
from app.db.session import close_db, create_engine_and_sessionmaker, init_db
from app.events.bus import EventBus
from app.events.store import EventStore
from app.execution.engine import ExecutionEngine
from app.git.workspace import GitWorktreeService
from app.roles.prompts import PromptBuilder
from app.roles.registry import RoleRegistry
from app.services.company import CompanyService
from app.services.memory import MemoryService
from app.services.settings import SettingsOverrides, SettingsStore, apply_overrides
from app.services.workflow import TaskWorkflowService
from app.workflows.manager import WorkflowManager

logger = logging.getLogger("sceneworks")


@dataclass
class AppContext:
    settings: Settings
    engine_factory: async_sessionmaker
    db_engine: AsyncEngine
    bus: EventBus
    event_store: EventStore
    backends: BackendRegistry
    git: GitWorktreeService
    roles: RoleRegistry
    prompt_builder: PromptBuilder
    execution_engine: ExecutionEngine
    workflow: TaskWorkflowService
    workflow_manager: WorkflowManager
    company: CompanyService
    memory: MemoryService
    settings_store: SettingsStore
    settings_overrides: SettingsOverrides
    health_warmup: asyncio.Task | None = field(default=None)

    async def shutdown(self) -> None:
        if self.health_warmup is not None and not self.health_warmup.done():
            self.health_warmup.cancel()
        await self.execution_engine.shutdown()
        await self.workflow_manager.shutdown()
        await close_db(self.db_engine)


async def _warm_backend_health(backends: BackendRegistry) -> None:
    try:
        await backends.health_all()
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - warm-up must never break startup
        logger.debug("backend health warm-up failed", exc_info=True)


async def build_context(settings: Settings | None = None) -> AppContext:
    settings = settings or get_settings()
    db_engine, session_factory = create_engine_and_sessionmaker(settings)
    await init_db(db_engine)

    settings_store = SettingsStore(session_factory)
    overrides = await settings_store.load()
    settings = apply_overrides(settings, overrides)

    bus = EventBus()
    event_store = EventStore(session_factory)
    backends = BackendRegistry(settings)
    git = GitWorktreeService(settings)
    roles = RoleRegistry(
        settings.roles_dir,
        default_backend=settings.default_backend if settings.default_backend != "gemini_acp" else None,
    )
    prompt_builder = PromptBuilder(settings, roles)
    execution_engine = ExecutionEngine(
        session_factory, bus, event_store, backends, settings
    )
    workflow = TaskWorkflowService(
        session_factory, execution_engine, git, prompt_builder, roles, bus, event_store, settings
    )
    memory = MemoryService(session_factory, event_store, bus)
    workflow_manager = WorkflowManager(
        session_factory,
        execution_engine,
        git,
        prompt_builder,
        roles,
        bus,
        event_store,
        settings,
        checkpoint_db_path=str(settings.checkpoint_db_path),
        max_review_iterations=settings.max_review_iterations,
        memory_service=memory,
    )
    execution_engine.on_execution_finished = workflow_manager.on_execution_finished
    company = CompanyService(session_factory, workflow, roles, git, prompt_builder, execution_engine)

    ctx = AppContext(
        settings=settings,
        engine_factory=session_factory,
        db_engine=db_engine,
        bus=bus,
        event_store=event_store,
        backends=backends,
        git=git,
        roles=roles,
        prompt_builder=prompt_builder,
        execution_engine=execution_engine,
        workflow=workflow,
        workflow_manager=workflow_manager,
        company=company,
        memory=memory,
        settings_store=settings_store,
        settings_overrides=overrides,
    )

    # Probe backend health in the background so the first dashboard/settings
    # request is served from cache instead of waiting on agent CLI startup.
    ctx.health_warmup = asyncio.create_task(_warm_backend_health(backends))

    interrupted = await execution_engine.recover_interrupted()
    if interrupted:
        logger.warning("reconciled %d interrupted executions from previous run", len(interrupted))
    recovered = await workflow_manager.recover_workflows()
    if recovered:
        logger.info("recovered %d workflows after restart", len(recovered))
    return ctx
