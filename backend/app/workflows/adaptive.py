"""Adaptive workflow routing with WP13 execution-intent constraints.

The existing workflow remains the execution engine. WP13 adds a user-visible
intent contract (auto/change/investigate/plan/ask) without creating parallel
workflow graphs. Explicit intent deterministically constrains LLM triage; auto
keeps triage-driven routing and records the resolved intent for provenance.
"""

from __future__ import annotations

import json
from typing import Any

from langgraph.graph import END, StateGraph

from app.domain.task_states import TaskStatus
from app.workflows.orchestrator import WorkflowManager as BaseWorkflowManager
from app.workflows.state import InitiativeState


class WorkflowManager(BaseWorkflowManager):
    """Public manager with conservative risk/latency and intent routing."""

    def _build_graph(self) -> StateGraph:
        """Build the base graph with Engineer as a legal post-advisor target."""
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
                "engineer": "engineer",
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

    async def _run_triage(self, state: InitiativeState) -> InitiativeState:
        result = await super()._run_triage(state)
        if result.get("task_status") in {TaskStatus.CANCELLED.value, TaskStatus.FAILED.value}:
            return result

        raw = result.get("triage_result")
        try:
            triage = json.loads(raw) if raw else {}
        except (TypeError, json.JSONDecodeError):
            triage = {}

        task_id = state["task_id"]
        async with self._session_factory() as session:
            task = await self._get_task(session, task_id)
            contract = task.engineering_contract or {}
            bounded = _bounded_contract(contract)
            request_type = str(triage.get("request_type") or result.get("request_type") or "feature")
            inferred_requires_implementation = bool(
                triage.get("requires_implementation", result.get("requires_implementation", True))
            )
            requested_mode = str(task.requested_mode or "auto").lower()
            resolved_mode = _resolve_execution_mode(
                requested_mode,
                request_type,
                inferred_requires_implementation,
            )
            mode_source = "inferred" if requested_mode == "auto" else "user"

            # Explicit modes are an authority boundary: the model can still
            # select useful advisory roles, but cannot silently turn a read-only
            # investigation/plan/answer into source modification or suppress a
            # requested change.
            if resolved_mode == "change":
                triage["requires_implementation"] = True
            elif resolved_mode in {"investigate", "plan"}:
                triage["requires_implementation"] = False
                triage["use_architect"] = True
            elif resolved_mode == "ask":
                triage.update(
                    {
                        "requires_implementation": False,
                        "use_product": False,
                        "use_cto": False,
                        "use_technical_expert": False,
                        "use_architect": True,
                    }
                )

            requires_implementation = bool(triage.get("requires_implementation", True))
            requested_architect = bool(triage.get("use_architect", True))
            task.resolved_mode = resolved_mode

            decision = "architecture"
            reason = "architecture retained"

            if requires_implementation:
                direct_bug = (
                    request_type == "bug"
                    and str(task.priority or "medium").lower() != "high"
                    and bounded
                )
                if direct_bug:
                    triage["use_architect"] = False
                    decision = "direct_implementation"
                    reason = (
                        "bounded bug: explicit allowed_scope, required_tests and "
                        "acceptance_criteria; Architect skipped to reduce redundant latency"
                    )
                    if task.status == TaskStatus.ARCHITECTURE_ANALYSIS.value:
                        await self._transition(task, "skip_architecture", "workflow-policy", session)
                    result["task_status"] = TaskStatus.READY_TO_IMPLEMENT.value
                elif not requested_architect:
                    triage["use_architect"] = True
                    decision = "architecture"
                    reason = "triage requested a skip but deterministic bounded-task gate did not pass"
            elif not requested_architect:
                decision = "advisory_only"
                reason = "no implementation required and no Architect selected"
                if task.status == TaskStatus.ARCHITECTURE_ANALYSIS.value:
                    await self._transition(task, "advisory_completed", "workflow-policy", session)
                result["task_status"] = TaskStatus.READY_FOR_HUMAN.value
            else:
                decision = f"read_only_{resolved_mode}"
                reason = f"{resolved_mode} mode requires read-only analysis and no Engineer execution"

            await session.commit()

        triage["requested_mode"] = requested_mode
        triage["resolved_mode"] = resolved_mode
        triage["mode_source"] = mode_source
        triage["routing_policy"] = {
            "decision": decision,
            "reason": reason,
            "bounded_contract": bounded,
            "requested_mode": requested_mode,
            "resolved_mode": resolved_mode,
            "mode_source": mode_source,
        }
        result["triage_result"] = json.dumps(triage)
        result["requires_implementation"] = bool(triage.get("requires_implementation", True))
        result["request_type"] = str(triage.get("request_type") or result.get("request_type") or "feature")
        await self._emit_workflow_event(
            task_id,
            "workflow.routing.policy",
            {
                "decision": decision,
                "reason": reason,
                "request_type": result["request_type"],
                "bounded_contract": bounded,
                "requested_mode": requested_mode,
                "resolved_mode": resolved_mode,
                "mode_source": mode_source,
            },
        )
        return result

    def _route_after_advisor_router(self, state: InitiativeState) -> str:
        if state.get("task_status") in {TaskStatus.CANCELLED.value, TaskStatus.FAILED.value}:
            return "__end__"

        triage = _triage_dict(state.get("triage_result"))
        for key, node, executed_key in (
            ("use_product", "product", "product_executed"),
            ("use_cto", "cto", "cto_executed"),
            ("use_technical_expert", "technical_expert", "technical_expert_executed"),
        ):
            if triage.get(key) and not state.get(executed_key):
                return node

        if triage.get("use_architect", True):
            return "architect"
        if bool(triage.get("requires_implementation", state.get("requires_implementation", True))):
            return "engineer"
        return "__end__"


def _resolve_execution_mode(
    requested_mode: str,
    request_type: str,
    requires_implementation: bool,
) -> str:
    """Resolve Auto from triage while treating explicit user intent as binding."""
    if requested_mode in {"change", "investigate", "plan", "ask"}:
        return requested_mode
    if requires_implementation:
        return "change"
    if request_type in {"architecture", "technology_decision"}:
        return "plan"
    if request_type == "product_question":
        return "ask"
    return "investigate"


def _bounded_contract(contract: dict[str, Any]) -> bool:
    return all(
        isinstance(contract.get(field), list) and bool(contract.get(field))
        for field in ("allowed_scope", "required_tests", "acceptance_criteria")
    )


def _triage_dict(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
