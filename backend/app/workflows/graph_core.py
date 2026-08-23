"""LangGraph-only workflow core (WP7).

This module owns graph topology, checkpointing, routing, and thin node
coordination. Operational work is supplied by the public orchestrator through
hook methods: persistence/events, triage/advisory execution, role execution,
founder controls, and recovery live in focused components.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Literal

from langgraph.graph import END, StateGraph
from langgraph.types import interrupt
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.domain.task_states import TaskStatus
from app.events import types as event_types
from app.git.workspace import GitError
from app.models import Execution
from app.services.workflow import WorkflowError, parse_review_verdict
from app.workflows.state import InitiativeState

logger = logging.getLogger("sceneworks.workflows.graph")

MAX_REVIEW_ITERATIONS_DEFAULT = 3
THREAD_PREFIX = "task"


class GraphWorkflowManager:
    """Graph/checkpoint lifecycle with operational behavior supplied by hooks."""

    def __init__(
        self,
        session_factory: async_sessionmaker,
        *,
        checkpoint_db_path: str = "data/workflow_checkpoints.db",
        max_review_iterations: int = MAX_REVIEW_ITERATIONS_DEFAULT,
    ) -> None:
        self._session_factory = session_factory
        self._max_review_iterations = max_review_iterations
        self._active_graphs: dict[int, asyncio.Task] = {}
        self._checkpoint_db_path = checkpoint_db_path
        self._checkpointer = None
        self._checkpointer_conn = None
        self._graph = self._build_graph()

    # ------------------------------------------------------------- lifecycle

    async def shutdown(self) -> None:
        """Stop running graph invocations before closing the checkpointer."""
        for task_id, graph_task in list(self._active_graphs.items()):
            if graph_task.done():
                continue
            graph_task.cancel()
            try:
                await graph_task
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001 - shutdown must not raise
                logger.debug("graph for task %s errored during shutdown", task_id)
        self._active_graphs.clear()

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

        builder.add_node("route_entry", self._node_route_entry)
        builder.add_node("advisor_router", self._node_advisor_router)
        builder.add_node("triage", self._node_triage)
        builder.add_node("product", self._node_product)
        builder.add_node("cto", self._node_cto)
        builder.add_node("technical_expert", self._node_technical_expert)
        builder.add_node("architect", self._node_architect)
        builder.add_node("architecture_approval", self._node_architecture_approval)
        builder.add_node("engineer", self._node_engineer)
        builder.add_node("reviewer", self._node_reviewer)

        builder.set_entry_point("route_entry")
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
        builder.add_edge("triage", "advisor_router")
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
        builder.add_edge("product", "advisor_router")
        builder.add_edge("cto", "advisor_router")
        builder.add_edge("technical_expert", "advisor_router")
        builder.add_conditional_edges(
            "architect",
            self._route_after_architect,
            {"architecture_approval": "architecture_approval", "__end__": END},
        )
        builder.add_conditional_edges(
            "architecture_approval",
            self._route_after_approval,
            {"architect": "architect", "engineer": "engineer", "__end__": END},
        )
        builder.add_conditional_edges(
            "engineer",
            self._route_after_engineer,
            {"reviewer": "reviewer", "__end__": END},
        )
        builder.add_conditional_edges(
            "reviewer",
            self._route_after_reviewer,
            {"engineer": "engineer", "__end__": END},
        )
        return builder

    async def _get_compiled_graph(self):
        checkpointer = await self._ensure_checkpointer()
        return self._graph.compile(checkpointer=checkpointer)

    # ------------------------------------------------------------- thin nodes

    async def _node_route_entry(self, state: InitiativeState) -> InitiativeState:
        return state

    async def _node_triage(self, state: InitiativeState) -> InitiativeState:
        task_id = state["task_id"]
        try:
            return await self._run_triage(state)
        except (WorkflowError, GitError) as exc:
            logger.error("triage preparation failed for task %s: %s", task_id, exc)
            await self._append_task_note(task_id, "triage could not start", str(exc))
            await self._set_task_status(task_id, TaskStatus.FAILED, "system")
            return {
                **state,
                "task_status": TaskStatus.FAILED.value,
                "triage_executed": True,
                "error": str(exc),
            }

    async def _node_advisor_router(self, state: InitiativeState) -> InitiativeState:
        return state

    async def _node_product(self, state: InitiativeState) -> InitiativeState:
        return await self._run_advisory_role(state, "product", "Product")

    async def _node_cto(self, state: InitiativeState) -> InitiativeState:
        return await self._run_advisory_role(state, "cto", "CTO")

    async def _node_technical_expert(self, state: InitiativeState) -> InitiativeState:
        return await self._run_advisory_role(
            state,
            "technical_expert",
            "Technical Expert",
        )

    async def _node_architect(self, state: InitiativeState) -> InitiativeState:
        task_id = state["task_id"]
        logger.info("architect node starting for task %s", task_id)
        try:
            execution = await self._prepare_and_start_architect(task_id, state)
        except (WorkflowError, GitError) as exc:
            logger.error("architect preparation failed for task %s: %s", task_id, exc)
            return {
                **state,
                "task_status": TaskStatus.FAILED.value,
                "error": str(exc),
            }

        await self._emit_workflow_event(
            task_id,
            "workflow.node.started",
            {"node": "architect", "execution_id": execution.id},
        )
        final = await self._wait_for_execution(execution.id)

        if final.status == "COMPLETED":
            requires_implementation = state.get("requires_implementation", True)
            await self._finish_architect(
                task_id,
                final,
                requires_implementation,
            )
            if not requires_implementation:
                await self._cleanup_architect_worktree(task_id)
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
        if final.status == "CANCELLED":
            return {
                **state,
                "task_status": TaskStatus.CANCELLED.value,
                "architecture_execution_id": final.id,
                "error": "cancelled",
            }

        await self._set_task_status(task_id, TaskStatus.FAILED, "system")
        return {
            **state,
            "task_status": TaskStatus.FAILED.value,
            "architecture_execution_id": final.id,
            "error": final.error,
        }

    async def _node_architecture_approval(
        self,
        state: InitiativeState,
    ) -> InitiativeState:
        task_id = state["task_id"]
        async with self._session_factory() as session:
            task = await self._get_task(session, task_id)
            current_db_status = task.status

        if current_db_status == TaskStatus.ARCHITECTURE_ANALYSIS.value:
            await self._set_task_status(
                task_id,
                TaskStatus.AWAITING_ARCHITECTURE_APPROVAL,
                "system",
            )
            await self._emit_workflow_event(
                task_id,
                "workflow.interrupted",
                {
                    "node": "architecture_approval",
                    "reason": "waiting for human approval",
                },
            )

        decision = interrupt(
            {
                "type": "architecture_approval",
                "task_id": task_id,
                "message": (
                    "Architecture analysis complete. Approve, reject, or request revision."
                ),
            }
        )
        action = decision.get("action") if isinstance(decision, dict) else str(decision)
        logger.info("architecture approval for task %s: %s", task_id, action)
        await self._emit_workflow_event(
            task_id,
            "workflow.resumed",
            {"node": "architecture_approval", "action": action},
        )

        if action == "approve":
            await self._approve_architecture_impl(task_id)
            return {
                **state,
                "task_status": TaskStatus.READY_TO_IMPLEMENT.value,
                "error": None,
            }
        if action == "reject":
            reason = decision.get("reason", "") if isinstance(decision, dict) else ""
            await self._reject_architecture_impl(task_id, reason)
            return {
                **state,
                "task_status": TaskStatus.REJECTED.value,
                "error": "rejected by founder",
            }
        if action == "revision":
            notes = decision.get("notes", "") if isinstance(decision, dict) else ""
            await self._request_revision_impl(task_id, notes)
            return {
                **state,
                "task_status": TaskStatus.ARCHITECTURE_ANALYSIS.value,
                "error": None,
            }

        logger.warning("unknown approval action %s for task %s", action, task_id)
        return {
            **state,
            "task_status": TaskStatus.AWAITING_ARCHITECTURE_APPROVAL.value,
            "error": f"unknown action: {action}",
        }

    async def _node_engineer(self, state: InitiativeState) -> InitiativeState:
        task_id = state["task_id"]
        is_correction = state.get("review_iteration", 0) > 0
        logger.info(
            "engineer node starting for task %s (correction=%s, iteration=%s)",
            task_id,
            is_correction,
            state.get("review_iteration", 0),
        )

        if is_correction:
            await self._emit_workflow_event(
                task_id,
                event_types.WORKFLOW_REPAIR_STARTED,
                {"iteration": state.get("review_iteration", 0)},
            )

        existing_exec_id = state.get("implementation_execution_id")
        if existing_exec_id:
            async with self._session_factory() as session:
                task_check = await self._get_task(session, task_id)
                if task_check.status in (
                    TaskStatus.IMPLEMENTING.value,
                    TaskStatus.TESTING.value,
                ):
                    existing = await session.get(Execution, existing_exec_id)
                    if existing is not None and existing.status in (
                        "COMPLETED",
                        "FAILED",
                        "CANCELLED",
                    ):
                        logger.info(
                            "engineer: reusing existing execution %s",
                            existing_exec_id,
                        )
                        return await self._engineer_terminal_state(state, existing)
                    if existing is not None and existing.status in (
                        "QUEUED",
                        "STARTING",
                        "RUNNING",
                    ):
                        logger.info(
                            "engineer: waiting for in-flight execution %s",
                            existing_exec_id,
                        )
                        final = await self._wait_for_execution(existing_exec_id)
                        return await self._engineer_terminal_state(state, final)

        try:
            execution = await self._prepare_and_start_engineer(
                task_id,
                is_correction,
            )
        except (WorkflowError, GitError) as exc:
            logger.error("engineer preparation failed for task %s: %s", task_id, exc)
            return {
                **state,
                "task_status": TaskStatus.FAILED.value,
                "error": str(exc),
            }

        await self._emit_workflow_event(
            task_id,
            "workflow.node.started",
            {"node": "engineer", "execution_id": execution.id},
        )
        final = await self._wait_for_execution(execution.id)
        result = await self._engineer_terminal_state(state, final)
        if final.status == "FAILED":
            await self._set_task_status(task_id, TaskStatus.FAILED, "system")
        return result

    async def _engineer_terminal_state(
        self,
        state: InitiativeState,
        execution: Execution,
    ) -> InitiativeState:
        task_id = state["task_id"]
        if execution.status == "COMPLETED":
            await self._finish_engineer(task_id, execution)
            return {
                **state,
                "task_status": TaskStatus.TESTING.value,
                "implementation_execution_id": execution.id,
                "implementation_result": execution.result,
                "error": None,
            }
        if execution.status == "CANCELLED":
            return {
                **state,
                "task_status": TaskStatus.CANCELLED.value,
                "implementation_execution_id": execution.id,
                "error": "cancelled",
            }
        return {
            **state,
            "task_status": TaskStatus.FAILED.value,
            "implementation_execution_id": execution.id,
            "error": execution.error,
        }

    async def _node_reviewer(self, state: InitiativeState) -> InitiativeState:
        task_id = state["task_id"]
        logger.info(
            "reviewer node starting for task %s (iteration %s)",
            task_id,
            state.get("review_iteration", 0),
        )
        try:
            execution = await self._prepare_and_start_reviewer(task_id)
        except (WorkflowError, GitError) as exc:
            logger.error("reviewer preparation failed for task %s: %s", task_id, exc)
            return {
                **state,
                "task_status": TaskStatus.FAILED.value,
                "error": str(exc),
            }

        await self._emit_workflow_event(
            task_id,
            "workflow.node.started",
            {"node": "reviewer", "execution_id": execution.id},
        )
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

            if iteration >= self._max_review_iterations:
                logger.warning(
                    "task %s exceeded max review iterations (%s/%s)",
                    task_id,
                    iteration,
                    self._max_review_iterations,
                )
                await self._emit_workflow_event(
                    task_id,
                    event_types.WORKFLOW_REPAIR_LIMIT_REACHED,
                    {"iteration": iteration, "max": self._max_review_iterations},
                )
                await self._append_task_note(
                    task_id,
                    "repair limit reached",
                    (
                        f"Maximum review iterations ({self._max_review_iterations}) "
                        "reached. Human intervention required."
                    ),
                )
            return {
                **state,
                "task_status": TaskStatus.CHANGES_REQUESTED.value,
                "review_execution_id": final.id,
                "review_result": final.result,
                "review_iteration": iteration,
                "error": None,
            }

        if final.status == "CANCELLED":
            return {
                **state,
                "task_status": TaskStatus.CANCELLED.value,
                "review_execution_id": final.id,
                "review_iteration": iteration,
                "error": "cancelled",
            }

        await self._set_task_status(task_id, TaskStatus.FAILED, "system")
        return {
            **state,
            "task_status": TaskStatus.FAILED.value,
            "review_execution_id": final.id,
            "review_iteration": iteration,
            "error": final.error,
        }

    # --------------------------------------------------------------- routing

    def _route_after_entry(
        self,
        state: InitiativeState,
    ) -> Literal["triage", "engineer", "reviewer", "__end__"]:
        status = state.get("task_status", "")
        triaged = state.get("triage_executed", False)
        if status in (TaskStatus.ARCHITECTURE_ANALYSIS.value, TaskStatus.NEW.value):
            return "triage" if not triaged else "__end__"
        if status in (
            TaskStatus.IMPLEMENTING.value,
            TaskStatus.CHANGES_REQUESTED.value,
        ):
            return "engineer"
        if status == TaskStatus.REVIEWING.value:
            return "reviewer"
        return "__end__"

    def _route_after_advisor_router(
        self,
        state: InitiativeState,
    ) -> Literal["product", "cto", "technical_expert", "architect", "__end__"]:
        if state.get("task_status") in (
            TaskStatus.FAILED.value,
            TaskStatus.CANCELLED.value,
        ):
            return "__end__"

        triage_json = state.get("triage_result", "{}")
        try:
            triage = json.loads(triage_json) if triage_json else {}
        except (json.JSONDecodeError, TypeError):
            triage = {}

        if triage.get("use_product") and not state.get("product_executed"):
            return "product"
        if triage.get("use_cto") and not state.get("cto_executed"):
            return "cto"
        if triage.get("use_technical_expert") and not state.get(
            "technical_expert_executed"
        ):
            return "technical_expert"
        if triage.get("use_architect", True):
            return "architect"
        return "__end__"

    def _route_after_architect(
        self,
        state: InitiativeState,
    ) -> Literal["architecture_approval", "__end__"]:
        if state.get("task_status", "") == TaskStatus.AWAITING_ARCHITECTURE_APPROVAL.value:
            return "architecture_approval"
        return "__end__"

    def _route_after_approval(
        self,
        state: InitiativeState,
    ) -> Literal["architect", "engineer", "__end__"]:
        status = state.get("task_status", "")
        if status == TaskStatus.ARCHITECTURE_ANALYSIS.value:
            return "architect"
        if status == TaskStatus.READY_TO_IMPLEMENT.value:
            return "engineer" if state.get("requires_implementation", True) else "__end__"
        return "__end__"

    def _route_after_engineer(
        self,
        state: InitiativeState,
    ) -> Literal["reviewer", "__end__"]:
        return (
            "reviewer"
            if state.get("task_status", "") == TaskStatus.TESTING.value
            else "__end__"
        )

    def _route_after_reviewer(
        self,
        state: InitiativeState,
    ) -> Literal["engineer", "__end__"]:
        if state.get("task_status", "") == TaskStatus.CHANGES_REQUESTED.value:
            if state.get("review_iteration", 0) < self._max_review_iterations:
                return "engineer"
        return "__end__"

    # ------------------------------------------------------------ graph run

    async def _launch_graph(self, input_data, config, task_id: int) -> None:
        try:
            graph = await self._get_compiled_graph()
            await self._run_graph(graph, input_data, config, task_id)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("graph setup failed for task %s", task_id)
            await self._emit_workflow_event(
                task_id,
                "workflow.failed",
                {"task_id": task_id, "reason": "graph setup error"},
            )
            await self._set_task_status(task_id, TaskStatus.FAILED, "system")
            self._active_graphs.pop(task_id, None)

    async def _run_graph(self, graph, input_data, config, task_id: int) -> None:
        try:
            await graph.ainvoke(input_data, config)
            await self._emit_workflow_event(
                task_id,
                "workflow.completed",
                {"task_id": task_id},
            )
        except asyncio.CancelledError:
            await self._emit_workflow_event(
                task_id,
                "workflow.failed",
                {"task_id": task_id, "reason": "cancelled"},
            )
        except Exception:  # noqa: BLE001
            logger.exception("graph execution failed for task %s", task_id)
            await self._emit_workflow_event(
                task_id,
                "workflow.failed",
                {"task_id": task_id, "reason": "graph error"},
            )
            await self._set_task_status(task_id, TaskStatus.FAILED, "system")
        finally:
            self._active_graphs.pop(task_id, None)
