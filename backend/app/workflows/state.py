"""Typed state carried through the LangGraph workflow.

The state holds references and small structured results only.  Large
artifacts (diffs, logs, repository state) stay in the database and
execution/event models.
"""

from __future__ import annotations

from typing import TypedDict


class InitiativeState(TypedDict):
    task_id: int
    project_id: int

    task_status: str

    # --- triage
    request_type: str | None
    triage_executed: bool
    triage_result: str | None
    requires_implementation: bool

    # --- optional company-role advisory results
    product_executed: bool
    product_execution_id: str | None
    product_result: str | None

    cto_executed: bool
    cto_execution_id: str | None
    cto_result: str | None

    technical_expert_executed: bool
    technical_expert_execution_id: str | None
    technical_expert_result: str | None

    # --- core workflow
    architecture_execution_id: str | None
    implementation_execution_id: str | None
    review_execution_id: str | None

    architecture_result: str | None
    implementation_result: str | None
    review_result: str | None

    review_iteration: int
    error: str | None
