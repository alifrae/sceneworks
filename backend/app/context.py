"""Application composition root.

Builds every service once at startup, wires the execution engine's continuation
hook to the LangGraph WorkflowManager, and reconciles interrupted executions,
legacy provider sessions, provider-neutral engineering sessions and managed PCS
runs.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.agents.model_routing import ModelRouter
from app.agents.registry import BackendRegistry
from app.config.settings import Settings, get_settings
from app.db.session import close_db, create_engine_and_sessionmaker, init_db
from app.events.bus import EventBus
from app.events.store import EventStore
from app.execution.engine import ExecutionEngine
from app.git.workspace import GitWorktreeService
from app.roles.prompts import PromptBuilder
from app.roles.registry import RoleRegistry
from app.runtime.registry import RuntimeRegistry
from app.services.agent_sessions import AgentSessionService
from app.services.company import CompanyService
from app.services.engineering_evidence import EngineeringEvidenceService
from app.services.engineering_sessions import EngineeringSessionService
from app.services.memory import MemoryService
from app.services.pcs_control import PcsControlService
from app.services.provenance import ProvenanceService
from app.services.settings import SettingsOverrides, SettingsStore, apply_overrides
from app.services.workflow import TaskWorkflowService
from app.workflows import WorkflowManager

logger = logging.getLogger("sceneworks")


@dataclass
class AppContext:
    settings: Settings
    engine_factory: async_sessionmaker
    db_engine: AsyncEngine
    bus: EventBus
    event_store: EventStore
    backends: BackendRegistry
    model_router: ModelRouter
    git: GitWorktreeService
    runtimes: RuntimeRegistry
    roles: RoleRegistry
    prompt_builder: PromptBuilder
    execution_engine: ExecutionEngine
    workflow: TaskWorkflowService
    workflow_manager: WorkflowManager
    company: CompanyService
    memory: MemoryService
    provenance: ProvenanceService
    agent_sessions: AgentSessionService
    engineering_sessions: EngineeringSessionService
    engineering_evidence: EngineeringEvidenceService
    pcs_control: PcsControlService
    settings_store: SettingsStore
    settings_overrides: SettingsOverrides
    health_warmup: asyncio.Task | None = field(default=None)

    async def shutdown(self) -> None:
        if self.health_warmup is not None and not self.health_warmup.done():
            self.health_warmup.cancel()
            try:
                await self.health_warmup
            except asyncio.CancelledError:
                pass
        await self.backends.shutdown()
        await self.agent_sessions.shutdown()
        # PCS control must drain/finalize managed runs before the native runtime
        # destroys its process handles.
        await self.pcs_control.shutdown()
        await self.runtimes.shutdown()
        await self.execution_engine.shutdown()
        await self.workflow_manager.shutdown()
        await close_db(self.db_engine)


async def _warm_backend_health(backends: BackendRegistry) -> None:
    try:
        await backends.health_all(force=True)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001
        logger.debug("backend health warm-up failed", exc_info=True)


async def build_context(settings: Settings | None = None) -> AppContext:
    settings = settings or get_settings()
    db_engine, session_factory = create_engine_and_sessionmaker(settings)
    await init_db(db_engine, settings)

    settings_store = SettingsStore(session_factory)
    overrides = await settings_store.load()
    settings = apply_overrides(settings, overrides)

    bus = EventBus()
    event_store = EventStore(session_factory)
    backends = BackendRegistry(settings)
    model_router = ModelRouter(settings, backends.keys())
    git = GitWorktreeService(settings)
    runtimes = RuntimeRegistry()
    roles = RoleRegistry(
        settings.roles_dir,
        default_backend=(
            settings.default_backend if settings.default_backend != "gemini_acp" else None
        ),
    )
    prompt_builder = PromptBuilder(settings, roles)
    execution_engine = ExecutionEngine(
        session_factory, bus, event_store, backends, settings
    )
    workflow = TaskWorkflowService(
        session_factory,
        execution_engine,
        git,
        prompt_builder,
        roles,
        bus,
        event_store,
        settings,
        model_router=model_router,
    )
    memory = MemoryService(session_factory, event_store, bus)
    provenance = ProvenanceService(session_factory, git)
    agent_sessions = AgentSessionService(session_factory, git, event_store, settings)
    engineering_sessions = EngineeringSessionService(
        session_factory, git, runtimes, settings
    )
    engineering_evidence = EngineeringEvidenceService(session_factory)
    pcs_control = PcsControlService(
        session_factory, engineering_sessions, engineering_evidence
    )
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
        model_router=model_router,
    )
    execution_engine.on_execution_finished = workflow_manager.on_execution_finished
    company = CompanyService(
        session_factory, workflow, roles, git, prompt_builder, execution_engine
    )

    ctx = AppContext(
        settings=settings,
        engine_factory=session_factory,
        db_engine=db_engine,
        bus=bus,
        event_store=event_store,
        backends=backends,
        model_router=model_router,
        git=git,
        runtimes=runtimes,
        roles=roles,
        prompt_builder=prompt_builder,
        execution_engine=execution_engine,
        workflow=workflow,
        workflow_manager=workflow_manager,
        company=company,
        memory=memory,
        provenance=provenance,
        agent_sessions=agent_sessions,
        engineering_sessions=engineering_sessions,
        engineering_evidence=engineering_evidence,
        pcs_control=pcs_control,
        settings_store=settings_store,
        settings_overrides=overrides,
    )

    ctx.health_warmup = asyncio.create_task(_warm_backend_health(backends))

    interrupted = await execution_engine.recover_interrupted()
    if interrupted:
        logger.warning("reconciled %d interrupted executions from previous run", len(interrupted))
    advanced_interrupted = await agent_sessions.recover_interrupted()
    if advanced_interrupted:
        logger.warning(
            "reconciled %d interrupted legacy provider sessions from previous run",
            len(advanced_interrupted),
        )
    engineering_interrupted = await engineering_sessions.recover_interrupted()
    if engineering_interrupted:
        logger.warning(
            "reconciled %d interrupted engineering-session creations from previous run",
            len(engineering_interrupted),
        )
    pcs_interrupted = await pcs_control.recover_interrupted()
    if pcs_interrupted:
        logger.warning(
            "marked %d managed PCS runs lost after SceneWorks restart",
            len(pcs_interrupted),
        )
    recovered = await workflow_manager.recover_workflows()
    if recovered:
        logger.info("recovered %d workflows after restart", len(recovered))
    return ctx
