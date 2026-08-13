"use client";

import { useState } from "react";
import { ACTION_INTENT, meaningfulActions } from "@/lib/workStages";

interface DecisionCardProps {
  allowedActions: string[];
  busy: boolean;
  pendingAction: string | null;
  onAction: (action: string, body?: Record<string, string>) => Promise<void>;
  /** Actions to omit from this card because they're offered elsewhere (e.g. cancel). */
  omit?: string[];
}

const CONFIRM: Record<string, string> = {
  reject_architecture: "Reject the architecture proposal?",
  reject: "Reject this work?",
};

// The explicit, well-defined set of ways a follow-up message can affect an
// in-flight or paused request (see docs/wp-web-2-conversation-model.md
// section C). Every action here maps 1:1 to a WorkflowManager transition —
// there is no free-text channel that could create the illusion a message
// reached a running agent.
export default function DecisionCard({ allowedActions, busy, pendingAction, onAction, omit = [] }: DecisionCardProps) {
  const [openNote, setOpenNote] = useState<string | null>(null);
  const [note, setNote] = useState("");

  const actions = meaningfulActions(allowedActions).filter((a) => !omit.includes(a));
  if (actions.length === 0) return null;

  async function trigger(action: string) {
    const intent = ACTION_INTENT[action];
    if (intent?.needsNote) {
      setOpenNote(action);
      return;
    }
    if (CONFIRM[action] && !window.confirm(CONFIRM[action])) return;
    await onAction(action);
  }

  return (
    <div className="decision-card">
      {openNote && (
        <div className="decision-note">
          <label className="field">
            {ACTION_INTENT[openNote]?.needsNote || "Notes"}
            <textarea
              rows={3}
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="Optional…"
              autoFocus
            />
          </label>
          <div className="row">
            <button
              className="btn primary"
              disabled={busy}
              onClick={async () => {
                await onAction(openNote, { reason: note, notes: note });
                setOpenNote(null);
                setNote("");
              }}
            >
              Send
            </button>
            <button className="btn small" onClick={() => setOpenNote(null)}>
              Cancel
            </button>
          </div>
        </div>
      )}
      <div className="row">
        {actions.map((action) => {
          const intent = ACTION_INTENT[action] || { label: action.replace(/_/g, " "), kind: "neutral" as const };
          return (
            <button
              key={action}
              className={`btn ${intent.kind === "primary" ? "primary" : intent.kind === "danger" ? "danger" : ""}`}
              disabled={busy}
              onClick={() => trigger(action)}
            >
              {busy && pendingAction === action ? "Working…" : intent.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}
