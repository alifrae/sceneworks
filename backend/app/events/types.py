"""Structured execution event types.

Only user-visible reasoning summaries and operational events are stored.
Raw model chain-of-thought is never persisted or exposed.

Event payloads are plain JSON dicts; each event carries execution_id and
timestamp in storage. `raw` fields from protocol adapters are kept behind
`diagnostics: true` so the UI can hide them by default.
"""

from __future__ import annotations

EXECUTION_STARTED = "execution.started"
EXECUTION_COMPLETED = "execution.completed"
EXECUTION_FAILED = "execution.failed"
EXECUTION_CANCELLED = "execution.cancelled"
EXECUTION_INTERRUPTED = "execution.interrupted"

AGENT_MESSAGE = "agent.message"
AGENT_TEXT_DELTA = "agent.text_delta"
AGENT_THOUGHT_SUMMARY = "agent.thought_summary"
AGENT_EVENT = "agent.event"

TOOL_STARTED = "tool.started"
TOOL_COMPLETED = "tool.completed"
COMMAND_STARTED = "command.started"
COMMAND_OUTPUT = "command.output"
FILE_CHANGED = "file.changed"
TEST_RESULT = "test.result"
GIT_COMMIT = "git.commit"

TASK_TRANSITIONED = "task.transitioned"
ARTIFACT_CREATED = "artifact.created"

WORKFLOW_STARTED = "workflow.started"
WORKFLOW_NODE_STARTED = "workflow.node.started"
WORKFLOW_NODE_COMPLETED = "workflow.node.completed"
WORKFLOW_INTERRUPTED = "workflow.interrupted"
WORKFLOW_RESUMED = "workflow.resumed"
WORKFLOW_COMPLETED = "workflow.completed"
WORKFLOW_FAILED = "workflow.failed"

# V2.2 workflow-orchestration events
WORKFLOW_TRIAGE_COMPLETED = "workflow.triage.completed"
WORKFLOW_ROLE_SELECTED = "workflow.role.selected"
WORKFLOW_ROLE_SKIPPED = "workflow.role.skipped"
WORKFLOW_REPAIR_STARTED = "workflow.repair.started"
WORKFLOW_REPAIR_LIMIT_REACHED = "workflow.repair.limit_reached"

# V2.4 memory events
MEMORY_CREATED = "memory.created"
MEMORY_UPDATED = "memory.updated"
MEMORY_ARCHIVED = "memory.archived"
MEMORY_SUPERSEDED = "memory.superseded"
MEMORY_INJECTED = "memory.injected"

# Human-facing labels used by the frontend to render events readably.
EVENT_LABELS: dict[str, str] = {
    EXECUTION_STARTED: "Execution started",
    EXECUTION_COMPLETED: "Execution completed",
    EXECUTION_FAILED: "Execution failed",
    EXECUTION_CANCELLED: "Execution cancelled",
    EXECUTION_INTERRUPTED: "Execution interrupted",
    AGENT_MESSAGE: "Agent message",
    AGENT_TEXT_DELTA: "Agent text",
    AGENT_THOUGHT_SUMMARY: "Agent reasoning summary",
    AGENT_EVENT: "Agent event",
    TOOL_STARTED: "Tool started",
    TOOL_COMPLETED: "Tool completed",
    COMMAND_STARTED: "Command started",
    COMMAND_OUTPUT: "Command output",
    FILE_CHANGED: "File changed",
    TEST_RESULT: "Test result",
    GIT_COMMIT: "Git commit",
    TASK_TRANSITIONED: "Task state changed",
    ARTIFACT_CREATED: "Artifact created",
    WORKFLOW_STARTED: "Workflow started",
    WORKFLOW_NODE_STARTED: "Workflow node started",
    WORKFLOW_NODE_COMPLETED: "Workflow node completed",
    WORKFLOW_INTERRUPTED: "Workflow paused (human input needed)",
    WORKFLOW_RESUMED: "Workflow resumed",
    WORKFLOW_COMPLETED: "Workflow completed",
    WORKFLOW_FAILED: "Workflow failed",
    WORKFLOW_TRIAGE_COMPLETED: "Triage completed",
    WORKFLOW_ROLE_SELECTED: "Role selected",
    WORKFLOW_ROLE_SKIPPED: "Role skipped",
    WORKFLOW_REPAIR_STARTED: "Repair iteration started",
    WORKFLOW_REPAIR_LIMIT_REACHED: "Repair limit reached",
    MEMORY_CREATED: "Memory created",
    MEMORY_UPDATED: "Memory updated",
    MEMORY_ARCHIVED: "Memory archived",
    MEMORY_SUPERSEDED: "Memory superseded",
    MEMORY_INJECTED: "Memory injected into workflow",
}
