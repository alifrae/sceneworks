// Human-readable labels for backend event types (see backend/app/events/types.py).

export const EVENT_LABELS: Record<string, string> = {
  "execution.started": "Execution started",
  "execution.completed": "Execution completed",
  "execution.failed": "Execution failed",
  "execution.cancelled": "Execution cancelled",
  "execution.interrupted": "Execution interrupted",
  "agent.message": "Agent message",
  "agent.text_delta": "Agent text",
  "agent.thought_summary": "Agent reasoning summary",
  "agent.event": "Agent event",
  "tool.started": "Tool started",
  "tool.completed": "Tool completed",
  "command.started": "Command started",
  "command.output": "Command output",
  "file.changed": "File changed",
  "test.result": "Test result",
  "git.commit": "Git commit",
  "task.transitioned": "Task state changed",
  "artifact.created": "Artifact created",
  "task.note": "Note",
};
