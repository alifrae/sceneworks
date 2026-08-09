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
}
