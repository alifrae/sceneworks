"""LangGraph workflow manager.

Orchestrates task workflows as LangGraph state graphs with durable
checkpointing. Nodes are thin adapters that delegate all real work
(worktree creation, execution, state persistence) to existing SceneWorks
services.

Agents, backends, worktree services, and event system must not depend on
LangGraph. LangGraph is an orchestration dependency only.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from langgraph.graph import END, StateGraph
from langgraph.types import Command, interrupt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config.settings import Settings
from app.domain.task_states import RUNNING, TaskStateMachine, TaskStatus
from app.events import types as event_types
from app.events.bus import EventBus
from app.events.store import EventStore
from app.execution.engine import ExecutionEngine
from app.git.workspace import GitWorktreeService
from app.models import Execution, Project, Task
from app.roles.definitions import RoleDefinition
from app.roles.prompts import PromptBuilder
from app.roles.registry import RoleRegistry
from app.services.memory import MemoryService
from app.services.workflow import COMPANY_ROLES, WorkflowError, parse_review_verdict
from app.workflows.state import InitiativeState

logger = logging.getLogger("sceneworks.workflows")

MAX_REVIEW_ITERATIONS_DEFAULT = 3
THREAD_PREFIX = "task"
TRIAGE_MODEL_PROFILE = "strongest"


class WorkflowManager:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        engine: ExecutionEngine,
        git: GitWorktreeService,
        prompt_builder: PromptBuilder,
        roles: RoleRegistry,
        bus: EventBus,
        event_store: EventStore,
        settings: Settings,
        checkpoint_db_path: str = "data/workflow_checkpoints.db",
        max_review_iterations: int = MAX_REVIEW_ITERATIONS_DEFAULT,
        memory_service: MemoryService | None = None,
    ):
        self._session_factory = session_factory
        self._engine = engine
        self._git = git
        self._prompts = prompt_builder
        self._roles = roles
        self._bus = bus
        self._events = event_store
        self._settings = settings
        self._max_review_iterations = max_review_iterations
        self._memory = memory_service

        self._pending_executions: dict[str, asyncio.Event] = {}
        self._active_graphs: dict[int, asyncio.Task] = {}

        self._checkpoint_db_path = checkpoint_db_path
        self._checkpointer = None
        self._checkpointer_conn = None
        self._graph = self._build_graph()

    async def shutdown(self) -> None:
        if self._checkpointer_conn is not None:
            await self._checkpointer_conn.close()
            self._checkpointer_conn = None
            self._checkpointer = None

    # ------------------------------------------------------------ checkpoint

    def _thread_id(self, task_id: int) -> str:
        return f"{THREAD_PREFIX}-{task_id}"

    def _config(self, task_id: int) -> dict:
        return {"configurable": {"thread_id": self._thread_id(task_id)}}

    async def _ensure_checkpointer(self):
        if self._checkpointer is not None:
            return self._checkpointer
        import aiosqlite

        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        path = self._checkpoint_db_path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._checkpointer_conn = await aiosqlite.connect(path)
        self._checkpointer = AsyncSqliteSaver(self._checkpointer_conn)
        await self._checkpointer.setup()
        return self._checkpointer

    # ------------------------------------------------------------ graph build

    def _build_graph(self) -> StateGraph:
        builder = StateGraph(InitiativeState)

        # Entry / router nodes
        builder.add_node("route_entry", self._node_route_entry)
        builder.add_node("advisor_router", self._node_advisor_router)

        # Triage
        builder.add_node("triage", self._node_triage)

        # Optional advisory roles
        builder.add_node("product", self._node_product)
        builder.add_node("cto", self._node_cto)
        builder.add_node("technical_expert", self._node_technical_expert)

        # Core workflow
        builder.add_node("architect", self._node_architect)
        builder.add_node("architecture_approval", self._node_architecture_approval)
        builder.add_node("engineer", self._node_engineer)
        builder.add_node("reviewer", self._node_reviewer)

        builder.set_entry_point("route_entry")

        # route_entry → triage / engineer / reviewer / end
        builder.add_conditional_edges(
            "route_entry",
            self._route_after_entry,
            {
                "triage": "triage",
                "engineer": "engineer",
                "reviewer": "reviewer",
                "__end__": END,
            },
        )

        # triage → advisor_router
        builder.add_edge("triage", "advisor_router")

        # advisor_router → product / cto / technical_expert / architect / end
        builder.add_conditional_edges(
            "advisor_router",
            self._route_after_advisor_router,
            {
                "product": "product",
                "cto": "cto",
                "technical_expert": "technical_expert",
                "architect": "architect",
                "__end__": END,
            },
        )

        # Each advisory role loops back to advisor_router
        builder.add_edge("product", "advisor_router")
        builder.add_edge("cto", "advisor_router")
        builder.add_edge("technical_expert", "advisor_router")

        # architect → approval / end (non-implementation)
        builder.add_conditional_edges(
            "architect",
            self._route_after_architect,
            {"architecture_approval": "architecture_approval", "__end__": END},
        )

        # approval → architect (revision) / engineer / end
        builder.add_conditional_edges(
            "architecture_approval",
            self._route_after_approval,
            {"architect": "architect", "engineer": "engineer", "__end__": END},
        )

        # engineer → reviewer / end
        builder.add_conditional_edges(
            "engineer",
            self._route_after_engineer,
            {"reviewer": "reviewer", "__end__": END},
        )

        # reviewer → engineer (auto-repair) / end
        builder.add_conditional_edges(
            "reviewer",
            self._route_after_reviewer,
            {"engineer": "engineer", "__end__": END},
        )

        return builder

    async def _get_compiled_graph(self):
        checkpointer = await self._ensure_checkpointer()
        return self._graph.compile(checkpointer=checkpointer)

    # ------------------------------------------------------------ entry node

    async def _node_route_entry(self, state: InitiativeState) -> InitiativeState:
        return state

    # ------------------------------------------------------------ triage node

    async def _node_triage(self, state: InitiativeState) -> InitiativeState:
        task_id = state["task_id"]
        logger.info("triage node starting for task %s", task_id)

        async with self._session_factory() as session:
            task = await self._get_task(session, task_id)
            project = await self._get_project(session, task.project_id)

        role_backend = self._roles.effective("architect").backend

        # When using the scripted fake backend, skip the triage model call
        # and use sensible defaults so tests don't require triage steps.
        if role_backend == "fake":
            triage_data = {
                "request_type": "feature",
                "use_product": False,
                "use_cto": False,
                "use_architect": True,
                "use_technical_expert": False,
                "requires_implementation": True,
                "reasoning_summary": "fake backend bypass; default architect path",
            }
        else:
            system, user = PromptBuilder.build_triage_prompt(task, project)

            memory_ctx = await self._inject_memory(
                project.id, task.description or task.title,
            )
            if memory_ctx.get("memories"):
                user = (
                    user + "\n\n## Project Memory (relevant decisions)\n"
                    + json.dumps(memory_ctx["memories"], indent=2)
                )
                await self._emit_workflow_event(
                    task_id, "memory.injected",
                    {"node": "triage", "injected_ids": memory_ctx.get("injected_ids", [])},
                )

            workspace = {
                "cwd": str(Path(project.repository_path).resolve()),
                "repo_path": str(Path(project.repository_path).resolve()),
                "branch": project.default_branch,
                "base_commit": task.base_commit,
                "permissions": ["repository_read", "network_access"],
            }

            role = RoleDefinition(
                key="triage",
                display_name="Triage",
                description="classifies request type and selects participants",
                backend=role_backend,
                model_profile=TRIAGE_MODEL_PROFILE,
            )

            async with self._session_factory() as session:
                task2 = await self._get_task(session, task_id)
                execution = await self._create_execution(
                    task=task2, role=role, workspace=workspace,
                    system_prompt=system, user_prompt=user,
                )
                task2.current_execution_id = execution.id
                task2.current_role = "triage"
                await session.commit()

            await self._emit_workflow_event(task_id, "workflow.node.started",
                                             {"node": "triage", "execution_id": execution.id})
            await self._engine.start(execution.id)
            final = await self._wait_for_execution(execution.id)
            triage_data = PromptBuilder.parse_triage_result(final.result or "")

        triage_json = json.dumps(triage_data)

        await self._emit_workflow_event(task_id, event_types.WORKFLOW_TRIAGE_COMPLETED,
                                         {"result": triage_data})

        # Emit role selection/skip events
        role_keys = [
            ("Product", "use_product", "product"),
            ("CTO", "use_cto", "cto"),
            ("Technical Expert", "use_technical_expert", "technical_expert"),
        ]
        for display, key, _r in role_keys:
            if triage_data.get(key):
                await self._emit_workflow_event(
                    task_id, event_types.WORKFLOW_ROLE_SELECTED,
                    {"role": _r, "display": display},
                )
            else:
                await self._emit_workflow_event(
                    task_id, event_types.WORKFLOW_ROLE_SKIPPED,
                    {"role": _r, "display": display},
                )

        return {
            **state,
            "triage_executed": True,
            "triage_result": triage_json,
            "request_type": triage_data.get("request_type", "feature"),
            "requires_implementation": triage_data.get("requires_implementation", True),
            "error": None,
        }

    # ------------------------------------------------------------ advisor router

    async def _node_advisor_router(self, state: InitiativeState) -> InitiativeState:
        return state

    def _route_after_advisor_router(
        self, state: InitiativeState,
    ) -> Literal["product", "cto", "technical_expert", "architect", "__end__"]:
        triage_json = state.get("triage_result", "{}")
        try:
            triage = json.loads(triage_json) if triage_json else {}
        except (json.JSONDecodeError, TypeError):
            triage = {}

        if triage.get("use_product") and not state.get("product_executed"):
            return "product"
        if triage.get("use_cto") and not state.get("cto_executed"):
            return "cto"
        if triage.get("use_technical_expert") and not state.get("technical_expert_executed"):
            return "technical_expert"
        if triage.get("use_architect", True):
            return "architect"
        return "__end__"

    # ------------------------------------------------------------ advisory nodes

    async def _node_product(self, state: InitiativeState) -> InitiativeState:
        return await self._run_advisory_role(state, "product", "Product")

    async def _node_cto(self, state: InitiativeState) -> InitiativeState:
        return await self._run_advisory_role(state, "cto", "CTO")

    async def _node_technical_expert(self, state: InitiativeState) -> InitiativeState:
        return await self._run_advisory_role(state, "technical_expert", "Technical Expert")

    async def _run_advisory_role(
        self, state: InitiativeState, role_key: str, display: str,
    ) -> InitiativeState:
        task_id = state["task_id"]
        logger.info("%s node starting for task %s", role_key, task_id)

        try:
            async with self._session_factory() as session:
                task = await self._get_task(session, task_id)
                project = await self._get_project(session, task.project_id)

                repo = Path(project.repository_path).resolve()
                info = await self._git.repo_info(repo)
                if not info.is_git:
                    raise WorkflowError(f"repository at {repo} is not a valid Git repository", 400)

                base = task.base_commit or await self._git.resolve_base_commit(
                    repo, project.default_branch
                )
                worktree = await self._git.create_detached_worktree(
                    repo, base, task.id, suffix=f"-{role_key}"
                )
                task.current_role = role_key

                role = self._roles.effective(role_key)
                workspace = {
                    "cwd": str(worktree.worktree_path),
                    "repo_path": str(repo),
                    "branch": None,
                    "base_commit": base,
                    "permissions": self._permission_names(role),
                }
                prompt = await self._prompts.build(
                    role=role, project=project, task=task, workspace=workspace,
                )
                execution = await self._create_execution(
                    task=task, role=role, workspace=workspace,
                    system_prompt=prompt.system, user_prompt=prompt.user,
                )
                task.current_execution_id = execution.id
                await session.commit()

            await self._emit_workflow_event(task_id, "workflow.node.started",
                                             {"node": role_key, "execution_id": execution.id})
            await self._engine.start(execution.id)
            final = await self._wait_for_execution(execution.id)

            if final.status == "COMPLETED":
                result_content = final.result or f"({display} produced no output)"
                await self._store_task_advisory_result(task_id, role_key, result_content)
                exec_field = f"{role_key}_execution_id"
                result_field = f"{role_key}_result"
                return {
                    **state,
                    f"{role_key}_executed": True,
                    exec_field: final.id,
                    result_field: result_content,
                    "error": None,
                }
            else:
                logger.warning("%s execution for task %s: %s", role_key, task_id, final.status)
                return {
                    **state,
                    f"{role_key}_executed": True,
                    f"{role_key}_execution_id": final.id,
                    f"{role_key}_result": f"({display} execution {final.status})",
                    "error": None,
                }
        except WorkflowError as exc:
            logger.error("%s preparation failed for task %s: %s", role_key, task_id, exc)
            return {
                **state,
                f"{role_key}_executed": True,
                "error": str(exc),
            }

    # ------------------------------------------------------------ architect node

    async def _node_architect(self, state: InitiativeState) -> InitiativeState:
        task_id = state["task_id"]
        logger.info("architect node starting for task %s", task_id)

        try:
            execution = await self._prepare_and_start_architect(task_id, state)
        except WorkflowError as exc:
            logger.error("architect preparation failed for task %s: %s", task_id, exc)
            return {
                **state,
                "task_status": TaskStatus.FAILED.value,
                "error": str(exc),
            }

        await self._emit_workflow_event(task_id, "workflow.node.started",
                                         {"node": "architect", "execution_id": execution.id})

        final = await self._wait_for_execution(execution.id)

        if final.status == "COMPLETED":
            await self._finish_architect(task_id, final)
            # Non-implementation tasks skip approval and go straight to ready.
            if not state.get("requires_implementation", True):
                await self._set_task_status(task_id, TaskStatus.READY_FOR_HUMAN, "system")
                return {
                    **state,
                    "task_status": TaskStatus.READY_FOR_HUMAN.value,
                    "architecture_execution_id": final.id,
                    "architecture_result": final.result,
                    "error": None,
                }
            return {
                **state,
                "task_status": TaskStatus.AWAITING_ARCHITECTURE_APPROVAL.value,
                "architecture_execution_id": final.id,
                "architecture_result": final.result,
                "error": None,
            }
        elif final.status == "CANCELLED":
            return {
                **state,
                "task_status": TaskStatus.CANCELLED.value,
                "architecture_execution_id": final.id,
                "error": "cancelled",
            }
        else:
            await self._set_task_status(task_id, TaskStatus.FAILED, "system")
            return {
                **state,
                "task_status": TaskStatus.FAILED.value,
                "architecture_execution_id": final.id,
                "error": final.error,
            }

    # ------------------------------------------------------------ architecture approval

    async def _node_architecture_approval(self, state: InitiativeState) -> InitiativeState:
        task_id = state["task_id"]

        async with self._session_factory() as session:
            task = await self._get_task(session, task_id)
            current_db_status = task.status

        if current_db_status == TaskStatus.ARCHITECTURE_ANALYSIS.value:
            await self._set_task_status(task_id, TaskStatus.AWAITING_ARCHITECTURE_APPROVAL, "system")
            await self._emit_workflow_event(task_id, "workflow.interrupted",
                                             {"node": "architecture_approval",
                                              "reason": "waiting for human approval"})

        decision = interrupt({
            "type": "architecture_approval",
            "task_id": task_id,
            "message": "Architecture analysis complete. Approve, reject, or request revision.",
        })

        action = decision.get("action") if isinstance(decision, dict) else str(decision)
        logger.info("architecture approval for task %s: %s", task_id, action)

        await self._emit_workflow_event(task_id, "workflow.resumed",
                                         {"node": "architecture_approval", "action": action})

        if action == "approve":
            await self._approve_architecture_impl(task_id)
            return {
                **state,
                "task_status": TaskStatus.READY_TO_IMPLEMENT.value,
                "error": None,
            }
        elif action == "reject":
            reason = decision.get("reason", "") if isinstance(decision, dict) else ""
            await self._reject_architecture_impl(task_id, reason)
            return {
                **state,
                "task_status": TaskStatus.REJECTED.value,
                "error": "rejected by founder",
            }
        elif action == "revision":
            notes = decision.get("notes", "") if isinstance(decision, dict) else ""
            await self._request_revision_impl(task_id, notes)
            return {
                **state,
                "task_status": TaskStatus.ARCHITECTURE_ANALYSIS.value,
                "error": None,
            }
        else:
            logger.warning("unknown approval action %s for task %s", action, task_id)
            return {
                **state,
                "task_status": TaskStatus.AWAITING_ARCHITECTURE_APPROVAL.value,
                "error": f"unknown action: {action}",
            }

    # ------------------------------------------------------------ engineer node

    async def _node_engineer(self, state: InitiativeState) -> InitiativeState:
        task_id = state["task_id"]
        is_correction = state.get("review_iteration", 0) > 0
        logger.info("engineer node starting for task %s (correction=%s, iteration=%s)",
                     task_id, is_correction, state.get("review_iteration", 0))

        if is_correction:
            await self._emit_workflow_event(
                task_id, event_types.WORKFLOW_REPAIR_STARTED,
                {"iteration": state.get("review_iteration", 0)},
            )

        # Idempotency: only reuse an existing execution when in an active
        # engineer state (IMPLEMENTING/TESTING). For correction iterations
        # the task is CHANGES_REQUESTED and needs a fresh execution.
        existing_exec_id = state.get("implementation_execution_id")
        if existing_exec_id:
            async with self._session_factory() as session:
                task_check = await self._get_task(session, task_id)
                if task_check.status in (TaskStatus.IMPLEMENTING.value, TaskStatus.TESTING.value):
                    existing = await session.get(Execution, existing_exec_id)
                    if existing is not None and existing.status in ("COMPLETED", "FAILED", "CANCELLED"):
                        logger.info("engineer: reusing existing execution %s", existing_exec_id)
                        if existing.status == "COMPLETED":
                            await self._finish_engineer(task_id, existing)
                            return {
                                **state,
                                "task_status": TaskStatus.TESTING.value,
                                "implementation_execution_id": existing.id,
                                "implementation_result": existing.result,
                                "error": None,
                            }
                        elif existing.status == "CANCELLED":
                            return {
                                **state,
                                "task_status": TaskStatus.CANCELLED.value,
                                "implementation_execution_id": existing.id,
                                "error": "cancelled",
                            }
                        else:
                            return {
                                **state,
                                "task_status": TaskStatus.FAILED.value,
                                "implementation_execution_id": existing.id,
                                "error": existing.error,
                            }
                    if existing is not None and existing.status in ("QUEUED", "STARTING", "RUNNING"):
                        logger.info("engineer: waiting for in-flight execution %s", existing_exec_id)
                        final = await self._wait_for_execution(existing_exec_id)
                        if final.status == "COMPLETED":
                            await self._finish_engineer(task_id, final)
                            return {
                                **state,
                                "task_status": TaskStatus.TESTING.value,
                                "implementation_execution_id": final.id,
                                "implementation_result": final.result,
                                "error": None,
                            }
                        elif final.status == "CANCELLED":
                            return {
                                **state,
                                "task_status": TaskStatus.CANCELLED.value,
                                "implementation_execution_id": final.id,
                                "error": "cancelled",
                            }
                        else:
                            return {
                                **state,
                                "task_status": TaskStatus.FAILED.value,
                                "implementation_execution_id": final.id,
                                "error": final.error,
                            }

        try:
            execution = await self._prepare_and_start_engineer(task_id, is_correction)
        except WorkflowError as exc:
            logger.error("engineer preparation failed for task %s: %s", task_id, exc)
            return {
                **state,
                "task_status": TaskStatus.FAILED.value,
                "error": str(exc),
            }

        await self._emit_workflow_event(task_id, "workflow.node.started",
                                         {"node": "engineer", "execution_id": execution.id})
        final = await self._wait_for_execution(execution.id)
        if final.status == "COMPLETED":
            await self._finish_engineer(task_id, final)
            return {
                **state,
                "task_status": TaskStatus.TESTING.value,
                "implementation_execution_id": final.id,
                "implementation_result": final.result,
                "error": None,
            }
        elif final.status == "CANCELLED":
            return {
                **state,
                "task_status": TaskStatus.CANCELLED.value,
                "implementation_execution_id": final.id,
                "error": "cancelled",
            }
        else:
            await self._set_task_status(task_id, TaskStatus.FAILED, "system")
            return {
                **state,
                "task_status": TaskStatus.FAILED.value,
                "implementation_execution_id": final.id,
                "error": final.error,
            }

    # ------------------------------------------------------------ reviewer node

    async def _node_reviewer(self, state: InitiativeState) -> InitiativeState:
        task_id = state["task_id"]
        logger.info("reviewer node starting for task %s (iteration %s)",
                     task_id, state.get("review_iteration", 0))

        try:
            execution = await self._prepare_and_start_reviewer(task_id)
        except WorkflowError as exc:
            logger.error("reviewer preparation failed for task %s: %s", task_id, exc)
            return {
                **state,
                "task_status": TaskStatus.FAILED.value,
                "error": str(exc),
            }

        await self._emit_workflow_event(task_id, "workflow.node.started",
                                         {"node": "reviewer", "execution_id": execution.id})

        final = await self._wait_for_execution(execution.id)

        iteration = state.get("review_iteration", 0) + 1

        if final.status == "COMPLETED":
            verdict = parse_review_verdict(final.result or "")
            await self._finish_reviewer(task_id, final, verdict)
            if verdict == "APPROVED":
                return {
                    **state,
                    "task_status": TaskStatus.READY_FOR_HUMAN.value,
                    "review_execution_id": final.id,
                    "review_result": final.result,
                    "review_iteration": iteration,
                    "error": None,
                }
            else:
                # CHANGES_REQUESTED — stay in that state; routing decides
                # whether to auto-repair or stop.
                if iteration >= self._max_review_iterations:
                    logger.warning(
                        "task %s exceeded max review iterations (%s/%s)",
                        task_id, iteration, self._max_review_iterations,
                    )
                    await self._emit_workflow_event(
                        task_id, event_types.WORKFLOW_REPAIR_LIMIT_REACHED,
                        {"iteration": iteration, "max": self._max_review_iterations},
                    )
                    await self._append_task_note(
                        task_id, "repair limit reached",
                        f"Maximum review iterations ({self._max_review_iterations}) reached. "
                        "Human intervention required.",
                    )
                return {
                    **state,
                    "task_status": TaskStatus.CHANGES_REQUESTED.value,
                    "review_execution_id": final.id,
                    "review_result": final.result,
                    "review_iteration": iteration,
                    "error": None,
                }
        elif final.status == "CANCELLED":
            return {
                **state,
                "task_status": TaskStatus.CANCELLED.value,
                "review_execution_id": final.id,
                "review_iteration": iteration,
                "error": "cancelled",
            }
        else:
            await self._set_task_status(task_id, TaskStatus.FAILED, "system")
            return {
                **state,
                "task_status": TaskStatus.FAILED.value,
                "review_execution_id": final.id,
                "review_iteration": iteration,
                "error": final.error,
            }

    # ------------------------------------------------------------ routing

    def _route_after_entry(
        self, state: InitiativeState,
    ) -> Literal["triage", "engineer", "reviewer", "__end__"]:
        status = state.get("task_status", "")
        triaged = state.get("triage_executed", False)
        if status in (TaskStatus.ARCHITECTURE_ANALYSIS.value, TaskStatus.NEW.value):
            if not triaged:
                return "triage"
            return "__end__"
        elif status in (TaskStatus.IMPLEMENTING.value, TaskStatus.CHANGES_REQUESTED.value):
            return "engineer"
        elif status == TaskStatus.REVIEWING.value:
            return "reviewer"
        return "__end__"

    def _route_after_architect(
        self, state: InitiativeState,
    ) -> Literal["architecture_approval", "__end__"]:
        status = state.get("task_status", "")
        if status == TaskStatus.AWAITING_ARCHITECTURE_APPROVAL.value:
            return "architecture_approval"
        return "__end__"

    def _route_after_approval(
        self, state: InitiativeState,
    ) -> Literal["architect", "engineer", "__end__"]:
        status = state.get("task_status", "")
        if status == TaskStatus.ARCHITECTURE_ANALYSIS.value:
            return "architect"
        elif status == TaskStatus.READY_TO_IMPLEMENT.value:
            if not state.get("requires_implementation", True):
                return "__end__"
            return "engineer"
        return "__end__"

    def _route_after_engineer(
        self, state: InitiativeState,
    ) -> Literal["reviewer", "__end__"]:
        status = state.get("task_status", "")
        if status == TaskStatus.TESTING.value:
            return "reviewer"
        return "__end__"

    def _route_after_reviewer(
        self, state: InitiativeState,
    ) -> Literal["engineer", "__end__"]:
        status = state.get("task_status", "")
        if status == TaskStatus.CHANGES_REQUESTED.value:
            iteration = state.get("review_iteration", 0)
            if iteration < self._max_review_iterations:
                return "engineer"
        return "__end__"

    # ------------------------------------------------------------ engine bridge

    async def on_execution_finished(self, execution_id: str) -> None:
        """Called by the engine when any execution terminates."""
        async with self._session_factory() as session:
            execution = await session.get(Execution, execution_id)
            if execution is None:
                return
            task = None
            if execution.task_id is not None:
                task = await session.get(Task, execution.task_id)

            if task is None and execution.role in COMPANY_ROLES:
                await self._store_company_artifact(session, execution)

        event = self._pending_executions.get(execution_id)
        if event:
            event.set()

    async def _wait_for_execution(self, execution_id: str) -> Execution:
        event = asyncio.Event()
        self._pending_executions[execution_id] = event
        try:
            await event.wait()
            async with self._session_factory() as session:
                row = await session.get(Execution, execution_id)
                if row is None:
                    raise RuntimeError(f"execution {execution_id} vanished")
                return row
        finally:
            self._pending_executions.pop(execution_id, None)

    # ------------------------------------------------------------ preparation

    async def _prepare_and_start_architect(
        self, task_id: int, state: InitiativeState | None = None,
    ) -> Execution:
        async with self._session_factory() as session:
            task = await self._get_task(session, task_id)
            project = await self._get_project(session, task.project_id)

            repo = Path(project.repository_path).resolve()
            info = await self._git.repo_info(repo)
            if not info.is_git:
                raise WorkflowError(f"repository at {repo} is not a valid Git repository", 400)

            base = await self._git.resolve_base_commit(repo, project.default_branch)
            worktree = await self._git.create_detached_worktree(repo, base, task.id)
            task.base_commit = base
            task.architecture_worktree_path = str(worktree.worktree_path)
            task.current_role = "architect"

            role = self._roles.effective("architect")
            workspace = {
                "cwd": str(worktree.worktree_path),
                "repo_path": str(repo),
                "branch": None,
                "base_commit": base,
                "permissions": self._permission_names(role),
            }

            # Build upstream context from advisory roles.
            upstream: dict[str, str] = {}
            if state:
                for label, key in [
                    ("Product requirements", "product_result"),
                    ("CTO recommendation", "cto_result"),
                    ("Technical Expert assessment", "technical_expert_result"),
                ]:
                    val = state.get(key)
                    if val:
                        upstream[label] = val

            memory_ctx = await self._inject_memory(
                project.id, task.description or task.title,
                types=["architecture_decision", "technology_decision", "constraint"],
            )
            if memory_ctx.get("memories"):
                upstream["Project Memory (relevant decisions/constraints)"] = json.dumps(
                    memory_ctx["memories"], indent=2
                )
                await self._emit_workflow_event(
                    task_id, "memory.injected",
                    {"node": "architect", "injected_ids": memory_ctx.get("injected_ids", [])},
                )

            prompt = await self._prompts.build(
                role=role, project=project, task=task, workspace=workspace,
                upstream_contexts=upstream or None,
            )
            execution = await self._create_execution(
                task=task, role=role, workspace=workspace,
                system_prompt=prompt.system, user_prompt=prompt.user,
            )
            task.current_execution_id = execution.id
            await session.commit()
        await self._engine.start(execution.id)
        return execution

    async def _prepare_and_start_engineer(
        self, task_id: int, is_correction: bool = False,
    ) -> Execution:
        async with self._session_factory() as session:
            task = await self._get_task(session, task_id)
            project = await self._get_project(session, task.project_id)

            if task.status not in (TaskStatus.IMPLEMENTING.value,):
                await self._transition(task, "start_implementation", "system", session)

            repo = Path(project.repository_path).resolve()
            info = await self._git.repo_info(repo)
            if not info.is_git:
                raise WorkflowError(f"repository at {repo} is not a valid Git repository", 400)

            branch = f"sw-task-{task.id}"
            base = task.base_commit or await self._git.resolve_base_commit(
                repo, project.default_branch
            )
            task.task_branch = branch
            if task.worktree_path and Path(task.worktree_path).is_dir():
                worktree_path = Path(task.worktree_path)
            else:
                worktree = await self._git.create_branch_worktree(repo, base, task.id, branch)
                worktree_path = worktree.worktree_path
                task.worktree_path = str(worktree_path)
            task.current_role = "engineer"

            role = self._roles.effective("engineer")
            workspace = {
                "cwd": str(worktree_path),
                "repo_path": str(repo),
                "branch": branch,
                "base_commit": base,
                "permissions": self._permission_names(role),
            }

            upstream: dict[str, str] = {}
            if task.architecture_result:
                upstream["Approved architecture"] = task.architecture_result
            async with self._session_factory() as s2:
                t2 = await self._get_task(s2, task_id)
                # Re-read to get latest review/arch results.
                if t2.review_result and is_correction:
                    upstream["Reviewer corrections to address"] = t2.review_result

            prompt = await self._prompts.build(
                role=role, project=project, task=task, workspace=workspace,
                is_correction=is_correction, upstream_contexts=upstream or None,
            )
            execution = await self._create_execution(
                task=task, role=role, workspace=workspace,
                system_prompt=prompt.system, user_prompt=prompt.user,
            )
            task.current_execution_id = execution.id
            await session.commit()
        await self._engine.start(execution.id)
        return execution

    async def _prepare_and_start_reviewer(self, task_id: int) -> Execution:
        async with self._session_factory() as session:
            task = await self._get_task(session, task_id)
            project = await self._get_project(session, task.project_id)

            if task.status not in (TaskStatus.REVIEWING.value,):
                await self._transition(task, "start_review", "system", session)

            if not task.worktree_path or not task.result_commit:
                raise WorkflowError("cannot review: no implementation commit found", 409)

            repo = Path(project.repository_path).resolve()

            # Clean up any leftover review worktree before creating a new one.
            if task.review_worktree_path:
                prev = Path(task.review_worktree_path)
                try:
                    await self._git.remove_worktree(repo, prev, None)
                except Exception:
                    import shutil
                    try:
                        shutil.rmtree(str(prev), ignore_errors=True)
                    except Exception:
                        pass
                task.review_worktree_path = None

            review_worktree = await self._git.create_detached_worktree(
                repo, task.result_commit, task.id, suffix="-review"
            )
            task.review_worktree_path = str(review_worktree.worktree_path)
            task.current_role = "reviewer"

            role = self._roles.effective("reviewer")
            workspace = {
                "cwd": str(review_worktree.worktree_path),
                "repo_path": str(repo),
                "branch": None,
                "base_commit": task.base_commit,
                "permissions": self._permission_names(role),
            }
            diff = await self._git.diff(
                Path(task.worktree_path), task.base_commit or task.result_commit
            )
            commits = await self._git.list_commits(
                Path(task.worktree_path), task.base_commit or task.result_commit
            )
            extra = {
                "Diff to review (base_commit..result_commit)": _cap(diff["full"], 120_000),
                "Commits": "\n".join(f"- {c['sha']} {c['subject']}" for c in commits),
            }
            prompt = await self._prompts.build(
                role=role, project=project, task=task, workspace=workspace, extra=extra
            )
            execution = await self._create_execution(
                task=task, role=role, workspace=workspace,
                system_prompt=prompt.system, user_prompt=prompt.user,
            )
            task.current_execution_id = execution.id
            await session.commit()
        await self._engine.start(execution.id)
        return execution

    # ------------------------------------------------------------ finish hooks

    async def _finish_architect(self, task_id: int, execution: Execution) -> None:
        async with self._session_factory() as session:
            task = await self._get_task(session, task_id)
            task.current_role = None
            task.current_execution_id = None
            task.architecture_result = execution.result or "(architect produced no analysis)"
            await self._transition(task, "architecture_completed", "system", session)

    async def _finish_engineer(self, task_id: int, execution: Execution) -> None:
        async with self._session_factory() as session:
            task = await self._get_task(session, task_id)
            task.current_role = None
            task.current_execution_id = None
            commit = None
            if task.worktree_path:
                try:
                    commit = await self._git.head_commit(Path(task.worktree_path))
                except Exception:
                    logger.warning("could not read engineer commit for task %s", task_id)
            task.result_commit = commit or execution.workspace.get("result_commit")
            task.implementation_summary = execution.result or ""
            await self._transition(task, "implementation_completed", "system", session)
            if commit:
                await self._events.append(
                    execution_id=execution.id,
                    task_id=task_id,
                    type=event_types.GIT_COMMIT,
                    payload={"commit": commit, "message": "Engineer implementation commit"},
                )

    async def _finish_reviewer(self, task_id: int, execution: Execution, verdict: str) -> None:
        async with self._session_factory() as session:
            task = await self._get_task(session, task_id)
            task.current_role = None
            task.current_execution_id = None
            task.review_result = execution.result or ""
            action = "review_completed" if verdict == "APPROVED" else "review_changes_requested"
            await self._transition(task, action, "system", session)
            await self._cleanup_review_worktree(task)

    async def _store_task_advisory_result(
        self, task_id: int, role_key: str, content: str,
    ) -> None:
        """Store an advisory role's output on the task model."""
        async with self._session_factory() as session:
            task = await self._get_task(session, task_id)
            task.current_role = None
            task.current_execution_id = None
            if role_key == "product":
                prefix = task.architecture_result or ""
                task.architecture_result = (
                    prefix + f"\n\n## Product Assessment\n{content[:20_000]}"
                ).strip()
            elif role_key == "cto":
                prefix = task.architecture_result or ""
                task.architecture_result = (
                    prefix + f"\n\n## CTO Assessment\n{content[:20_000]}"
                ).strip()
            elif role_key == "technical_expert":
                prefix = task.architecture_result or ""
                task.architecture_result = (
                    prefix + f"\n\n## Technical Expert Assessment\n{content[:20_000]}"
                ).strip()
            await session.commit()

    async def _cleanup_review_worktree(self, task: Task) -> None:
        path = task.review_worktree_path
        if not path:
            return
        async with self._session_factory() as session:
            project = await self._get_project(session, task.project_id)
            try:
                await self._git.remove_worktree(Path(project.repository_path), Path(path), None)
            except Exception:
                logger.warning("review worktree cleanup failed for task %s", task.id)
            task_row = await session.get(Task, task.id)
            task_row.review_worktree_path = None
            await session.commit()

    # ------------------------------------------------------------ approval impl

    async def _approve_architecture_impl(self, task_id: int) -> None:
        async with self._session_factory() as session:
            task = await self._get_task(session, task_id)
            if task.status == TaskStatus.AWAITING_ARCHITECTURE_APPROVAL.value:
                await self._transition(task, "approve_architecture", "founder", session)
        await self._cleanup_architect_worktree(task_id)

    async def _reject_architecture_impl(self, task_id: int, reason: str) -> None:
        async with self._session_factory() as session:
            task = await self._get_task(session, task_id)
            if task.status == TaskStatus.AWAITING_ARCHITECTURE_APPROVAL.value:
                await self._transition(task, "reject_architecture", "founder", session)
        await self._cleanup_architect_worktree(task_id)
        if reason:
            await self._append_task_note(task_id, "architecture rejected", reason)

    async def _request_revision_impl(self, task_id: int, notes: str) -> None:
        async with self._session_factory() as session:
            task = await self._get_task(session, task_id)
            if task.status == TaskStatus.AWAITING_ARCHITECTURE_APPROVAL.value:
                await self._transition(task, "request_architecture_revision", "founder", session)
        await self._cleanup_architect_worktree(task_id)
        if notes:
            await self._append_task_note(task_id, "architecture revision requested", notes)

    async def _cleanup_architect_worktree(self, task_id: int) -> None:
        async with self._session_factory() as session:
            task = await self._get_task(session, task_id)
            path = task.architecture_worktree_path
            repo = (await self._get_project(session, task.project_id)).repository_path
            task.architecture_worktree_path = None
            await session.commit()
        if path:
            try:
                await self._git.remove_worktree(Path(repo), Path(path), None)
            except Exception:
                logger.warning("architect worktree cleanup failed for task %s", task_id)

    # ------------------------------------------------------------ public API

    async def start_workflow(self, task_id: int) -> None:
        """Start the workflow graph for a task (background)."""
        async with self._session_factory() as session:
            task = await self._get_task(session, task_id)
            project = await self._get_project(session, task.project_id)
            await self._transition(task, "start_architecture", "founder", session)
            project_id = task.project_id

        graph = await self._get_compiled_graph()
        config = self._config(task_id)

        initial: InitiativeState = {
            "task_id": task_id,
            "project_id": project_id,
            "task_status": TaskStatus.ARCHITECTURE_ANALYSIS.value,
            "request_type": None,
            "triage_executed": False,
            "triage_result": None,
            "requires_implementation": True,
            "product_executed": False,
            "product_execution_id": None,
            "product_result": None,
            "cto_executed": False,
            "cto_execution_id": None,
            "cto_result": None,
            "technical_expert_executed": False,
            "technical_expert_execution_id": None,
            "technical_expert_result": None,
            "architecture_execution_id": None,
            "implementation_execution_id": None,
            "review_execution_id": None,
            "architecture_result": None,
            "implementation_result": None,
            "review_result": None,
            "review_iteration": 0,
            "error": None,
        }

        await self._emit_workflow_event(task_id, "workflow.started", {"task_id": task_id})

        task_coro = asyncio.create_task(
            self._run_graph(graph, initial, config, task_id),
            name=f"wf-{task_id}",
        )
        self._active_graphs[task_id] = task_coro

    async def resume_approval(self, task_id: int, action: str, reason: str = "") -> None:
        """Resume a paused graph with an approval action."""

        await self._await_previous_graph(task_id)

        async with self._session_factory() as session:
            task = await self._get_task(session, task_id)
            if action == "approve":
                await self._transition(task, "approve_architecture", "founder", session)
            elif action == "reject":
                await self._transition(task, "reject_architecture", "founder", session)
            elif action == "revision":
                await self._transition(task, "request_architecture_revision", "founder", session)

        graph = await self._get_compiled_graph()
        config = self._config(task_id)

        decision = {"action": action}
        if action == "reject":
            decision["reason"] = reason
        elif action == "revision":
            decision["notes"] = reason

        task_asyncio = asyncio.create_task(
            self._run_graph(graph, Command(resume=decision), config, task_id),
            name=f"wf-{task_id}-resume",
        )
        self._active_graphs[task_id] = task_asyncio

    async def start_implementation(self, task_id: int) -> None:
        """Trigger engineer phase via graph (background)."""
        await self._await_previous_graph(task_id)

        async with self._session_factory() as session:
            task = await self._get_task(session, task_id)
            if task.status not in (TaskStatus.IMPLEMENTING.value,):
                if TaskStateMachine.can_transition(TaskStatus(task.status), "start_implementation"):
                    await self._transition(task, "start_implementation", "founder", session)
                elif task.status in (
                    TaskStatus.TESTING.value,
                    TaskStatus.REVIEWING.value,
                    TaskStatus.READY_FOR_HUMAN.value,
                    TaskStatus.ACCEPTED.value,
                ):
                    # Already past implementation — idempotent no-op.
                    return
                else:
                    raise WorkflowError(
                        f"invalid transition: task is {task.status}, "
                        "cannot start implementation", 409,
                    )

        graph = await self._get_compiled_graph()
        config = self._config(task_id)

        cmd = Command(
            update={
                "task_id": task_id,
                "project_id": task.project_id,
                "task_status": TaskStatus.IMPLEMENTING.value,
                "error": None,
            },
            goto="route_entry",
        )

        task_asyncio = asyncio.create_task(
            self._run_graph(graph, cmd, config, task_id),
            name=f"wf-{task_id}-impl",
        )
        self._active_graphs[task_id] = task_asyncio

    async def start_review(self, task_id: int) -> None:
        """Trigger reviewer phase via graph (background)."""
        await self._await_previous_graph(task_id)

        async with self._session_factory() as session:
            task = await self._get_task(session, task_id)
            if task.status not in (TaskStatus.REVIEWING.value,):
                await self._transition(task, "start_review", "founder", session)

        graph = await self._get_compiled_graph()
        config = self._config(task_id)

        cmd = Command(
            update={
                "task_id": task_id,
                "project_id": task.project_id,
                "task_status": TaskStatus.REVIEWING.value,
                "error": None,
            },
            goto="route_entry",
        )

        task_asyncio = asyncio.create_task(
            self._run_graph(graph, cmd, config, task_id),
            name=f"wf-{task_id}-review",
        )
        self._active_graphs[task_id] = task_asyncio

    async def accept(self, task_id: int) -> None:
        async with self._session_factory() as session:
            task = await self._get_task(session, task_id)
            await self._transition(task, "accept", "founder", session)

    async def reject(self, task_id: int, reason: str = "") -> None:
        async with self._session_factory() as session:
            task = await self._get_task(session, task_id)
            await self._transition(task, "reject", "founder", session)
        if reason:
            await self._append_task_note(task_id, "work rejected", reason)

    async def send_back_to_engineer(self, task_id: int, notes: str = "") -> None:
        async with self._session_factory() as session:
            task = await self._get_task(session, task_id)
            await self._transition(task, "send_back_to_engineer", "founder", session)
        if notes:
            await self._append_task_note(task_id, "sent back to engineer", notes)

        graph = await self._get_compiled_graph()
        config = self._config(task_id)

        cmd = Command(
            update={
                "task_id": task_id,
                "project_id": 0,
                "task_status": TaskStatus.CHANGES_REQUESTED.value,
                "error": None,
            },
            goto="route_entry",
        )

        task_asyncio = asyncio.create_task(
            self._run_graph(graph, cmd, config, task_id),
            name=f"wf-{task_id}-sendback",
        )
        self._active_graphs[task_id] = task_asyncio

    async def cancel(self, task_id: int) -> None:
        async with self._session_factory() as session:
            task = await self._get_task(session, task_id)
            execution_id = task.current_execution_id
            await self._transition(task, "cancel", "founder", session)
        if execution_id:
            await self._engine.cancel(execution_id)
        self._active_graphs.pop(task_id, None)

    async def retry(self, task_id: int) -> None:
        async with self._session_factory() as session:
            task = await self._get_task(session, task_id)
            if task.status != TaskStatus.FAILED.value:
                raise WorkflowError("retry is only available for FAILED tasks", 409)

        if task.architecture_result:
            await self.start_implementation(task_id)
        else:
            await self.start_workflow(task_id)

    async def cleanup_worktree(self, task_id: int) -> None:
        async with self._session_factory() as session:
            task = await self._get_task(session, task_id)
            if task.status in RUNNING:
                raise WorkflowError("cannot clean up while the task is running", 409)
            repo = (await self._get_project(session, task.project_id)).repository_path
            worktree_path = task.worktree_path
            branch = task.task_branch
            task.worktree_path = None
            task.task_branch = None
            await session.commit()
        if worktree_path:
            await self._git.remove_worktree(Path(repo), Path(worktree_path), branch)
        await self._append_task_note(task_id, "worktree cleaned up", "")

    # ------------------------------------------------------------ graph runner

    async def _run_graph(self, graph, input_data, config, task_id: int) -> None:
        try:
            await graph.ainvoke(input_data, config)
            await self._emit_workflow_event(task_id, "workflow.completed",
                                             {"task_id": task_id})
        except asyncio.CancelledError:
            await self._emit_workflow_event(task_id, "workflow.failed",
                                             {"task_id": task_id, "reason": "cancelled"})
        except Exception:
            logger.exception("graph execution failed for task %s", task_id)
            await self._emit_workflow_event(task_id, "workflow.failed",
                                             {"task_id": task_id, "reason": "graph error"})
            await self._set_task_status(task_id, TaskStatus.FAILED, "system")
        finally:
            self._active_graphs.pop(task_id, None)

    # ------------------------------------------------------------ helpers

    async def _await_previous_graph(self, task_id: int) -> None:
        """Wait for any previous graph run for this task to finish."""
        prev = self._active_graphs.get(task_id)
        if prev is not None and not prev.done():
            try:
                await prev
            except Exception:
                pass

    async def _get_task(self, session: AsyncSession, task_id: int) -> Task:
        task = await session.get(Task, task_id)
        if task is None:
            raise WorkflowError(f"task {task_id} not found", 404)
        return task

    async def _get_project(self, session: AsyncSession, project_id: int) -> Project:
        project = await session.get(Project, project_id)
        if project is None:
            raise WorkflowError(f"project {project_id} not found", 404)
        return project

    async def _transition(
        self, task: Task, action: str, actor: str, session: AsyncSession,
    ) -> TaskStatus:
        current = TaskStatus(task.status)
        try:
            new_status = TaskStateMachine.transition(current, action)
        except Exception as exc:
            raise WorkflowError(
                f"invalid transition: task is {current.value}, "
                f"cannot apply action {action!r}", 409,
            ) from exc
        task.status = new_status.value
        task.updated_at = datetime.now(timezone.utc)
        await session.commit()
        await self._events.append(
            execution_id=None,
            task_id=task.id,
            type=event_types.TASK_TRANSITIONED,
            payload={"from": current.value, "to": new_status.value,
                      "action": action, "actor": actor},
        )
        await self._bus.publish({
            "id": 0,
            "execution_id": None,
            "task_id": task.id,
            "type": event_types.TASK_TRANSITIONED,
            "payload": {"from": current.value, "to": new_status.value,
                         "action": action, "actor": actor},
            "severity": "info",
        })
        return new_status

    async def _set_task_status(self, task_id: int, status: TaskStatus, actor: str = "system") -> None:
        async with self._session_factory() as session:
            task = await self._get_task(session, task_id)
            try:
                existing = TaskStatus(task.status)
            except ValueError:
                existing = None
            task.status = status.value
            task.updated_at = datetime.now(timezone.utc)
            await session.commit()
            await self._events.append(
                execution_id=None,
                task_id=task_id,
                type=event_types.TASK_TRANSITIONED,
                payload={"from": existing.value if existing else "unknown",
                          "to": status.value, "action": "workflow", "actor": actor},
            )
            await self._bus.publish({
                "id": 0,
                "execution_id": None,
                "task_id": task_id,
                "type": event_types.TASK_TRANSITIONED,
                "payload": {"from": existing.value if existing else "unknown",
                             "to": status.value, "action": "workflow", "actor": actor},
                "severity": "info",
            })

    async def _create_execution(
        self, *, task: Task, role: RoleDefinition, workspace: dict,
        system_prompt: str, user_prompt: str,
    ) -> Execution:
        import uuid

        execution = Execution(
            id=uuid.uuid4().hex,
            task_id=task.id,
            role=role.key,
            backend=role.backend,
            model_profile=role.model_profile,
            status="QUEUED",
            workspace=workspace,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            prompt_preview=user_prompt[:2000],
        )
        async with self._session_factory() as session:
            session.add(execution)
            await session.commit()
            await session.refresh(execution)
        return execution

    async def _append_task_note(self, task_id: int, title: str, detail: str) -> None:
        await self._events.append(
            execution_id=None,
            task_id=task_id,
            type="task.note",
            payload={"title": title, "detail": detail[:4000]},
        )

    def _permission_names(self, role: RoleDefinition) -> list[str]:
        return sorted(p.value for p in role.permissions)

    async def _emit_workflow_event(self, task_id: int, event_type: str, payload: dict) -> None:
        await self._events.append(
            execution_id=None,
            task_id=task_id,
            type=event_type,
            payload=payload,
        )

    async def _inject_memory(
        self, project_id: int, task_description: str, types: list[str] | None = None,
    ) -> dict:
        if self._memory is None:
            return {"memories": [], "injected_ids": []}
        ctx = await self._memory.injection_context(
            project_id, task_description, types=types,
        )
        return ctx

    # ------------------------------------------------------------ company artifacts

    async def _store_company_artifact(self, session: AsyncSession, execution: Execution) -> None:
        from app.models import Artifact

        workspace = execution.workspace or {}
        title = workspace.get("ask_title") or f"{execution.role} decision"
        content = execution.result or f"Execution failed: {execution.error or 'unknown'}"
        artifact = Artifact(
            kind="company_decision",
            role=execution.role,
            project_id=workspace.get("project_id") if isinstance(workspace.get("project_id"), int) else None,
            title=title,
            content=content,
            source_execution_id=execution.id,
        )
        session.add(artifact)
        await session.commit()
        await self._events.append(
            execution_id=execution.id,
            task_id=None,
            type=event_types.ARTIFACT_CREATED,
            payload={"artifact_id": artifact.id, "role": execution.role},
        )


def _cap(text: str, max_bytes: int) -> str:
    if len(text.encode("utf-8")) <= max_bytes:
        return text
    return text.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore") + "\n[truncated]"
