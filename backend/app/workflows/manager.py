"""Backward-compatible WorkflowManager import.

WP7 decomposed the former monolithic manager into a LangGraph-only core plus
focused runtime, role, advisory, control, and recovery components. WP11 then
made the adaptive subclass the public orchestrator. New code should import
``WorkflowManager`` from ``app.workflows``; this module remains only so older
imports resolve to that same public manager rather than silently bypassing the
adaptive routing policy.
"""

from app.workflows.adaptive import WorkflowManager
from app.workflows.advisory_runtime import TRIAGE_MODEL_PROFILE
from app.workflows.formatting import cap_text, format_memory_block
from app.workflows.graph_core import MAX_REVIEW_ITERATIONS_DEFAULT, THREAD_PREFIX

# Private compatibility aliases retained for older tests/extensions that
# imported formatting helpers from the pre-WP7 monolith.
_format_memory_block = format_memory_block
_cap = cap_text

__all__ = [
    "WorkflowManager",
    "MAX_REVIEW_ITERATIONS_DEFAULT",
    "THREAD_PREFIX",
    "TRIAGE_MODEL_PROFILE",
]
