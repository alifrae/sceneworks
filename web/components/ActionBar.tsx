"use client";

import { useState } from "react";

interface ActionBarProps {
  taskId: number;
  allowedActions: string[];
  onAction: (action: string, body?: Record<string, string>) => Promise<void>;
  busy: boolean;
  pendingAction?: string | null;
}

const LABELS: Record<string, string> = {
  start_architecture: "Run Architect analysis",
  approve_architecture: "Approve architecture",
  reject_architecture: "Reject architecture",
  request_architecture_revision: "Request revision",
  start_implementation: "Start implementation",
  start_review: "Start review",
  accept: "Accept",
  reject: "Reject",
  send_back_to_engineer: "Send back to Engineer",
  cancel: "Stop",
  retry: "Retry",
  cleanup_worktree: "Clean up worktree",
};

const CONFIRM: Record<string, string> = {
  reject_architecture: "Reject the architecture proposal?",
  reject: "Reject this work?",
  cleanup_worktree: "Remove the task worktree and branch?",
};

const PROGRESS_LABELS: Record<string, string> = {
  start_architecture: "Starting…",
  approve_architecture: "Approving…",
  reject_architecture: "Rejecting…",
  request_architecture_revision: "Requesting revision…",
  start_implementation: "Starting implementation…",
  start_review: "Starting review…",
  accept: "Accepting…",
  reject: "Rejecting…",
  send_back_to_engineer: "Sending back…",
  cancel: "Stopping…",
  retry: "Retrying…",
  cleanup_worktree: "Cleaning up…",
};

export default function ActionBar({ allowedActions, onAction, busy, pendingAction }: ActionBarProps) {
  const [notes, setNotes] = useState("");
  const [noteFor, setNoteFor] = useState<string | null>(null);

  async function run(action: string, needsNotes: boolean) {
    if (needsNotes) {
      setNoteFor(action);
      return;
    }
    if (CONFIRM[action] && !window.confirm(CONFIRM[action])) return;
    await onAction(action);
  }

  const noteActions: Record<string, string> = {
    request_architecture_revision: "Notes for the Architect",
    reject_architecture: "Reason",
    reject: "Reason",
    send_back_to_engineer: "Notes for the Engineer",
  };

  return (
    <div>
      {noteFor && (
        <div className="panel" style={{ marginBottom: 10 }}>
          <label className="field">
            {noteActions[noteFor]}
            <textarea
              rows={3}
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="Optional…"
            />
          </label>
          <div className="row">
            <button
              className="btn primary"
              disabled={busy}
              onClick={async () => {
                await onAction(noteFor, { reason: notes, notes });
                setNoteFor(null);
                setNotes("");
              }}
            >
              Confirm
            </button>
            <button className="btn small" onClick={() => setNoteFor(null)}>
              Cancel
            </button>
          </div>
        </div>
      )}
      <div className="row">
        {allowedActions.map((action) => (
          <button
            key={action}
            className={`btn ${
              action === "accept" || action === "approve_architecture"
                ? "primary"
                : action === "cancel" || action === "reject" || action === "reject_architecture"
                  ? "danger"
                  : ""
            }`}
            disabled={busy}
            onClick={() => run(action, action in noteActions)}
          >
            {busy && pendingAction === action
              ? PROGRESS_LABELS[action] ?? "Working…"
              : LABELS[action] ?? action.replace(/_/g, " ")}
          </button>
        ))}
        {allowedActions.length === 0 && <span className="muted small">No actions available.</span>}
      </div>
    </div>
  );
}
