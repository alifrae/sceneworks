"use client";

import type { Task } from "@/lib/types";
import { ROLE_LABELS, getWorkView } from "@/lib/workStages";

interface RoleRow {
  key: string;
  label: string;
  state: "active" | "done" | "waiting";
  note: string;
}

// Request-specific role ownership for this Work Thread only — broader team
// information lives on the Team page (see /company).
export default function RoleStatusPanel({ task }: { task: Task }) {
  const advisory = getWorkView(task).isAdvisoryOnly;
  const terminal = ["ACCEPTED", "REJECTED", "CANCELLED", "FAILED"].includes(task.status);

  const rows: RoleRow[] = [
    {
      key: "architect",
      label: ROLE_LABELS.architect,
      state: task.current_role === "architect" ? "active" : task.architecture_result ? "done" : "waiting",
      note: task.current_role === "architect" ? "Investigating repository architecture" : task.architecture_result ? "Analysis delivered" : "Waiting to start",
    },
  ];

  if (!advisory) {
    rows.push(
      {
        key: "engineer",
        label: ROLE_LABELS.engineer,
        state: task.current_role === "engineer" ? "active" : task.implementation_summary ? "done" : "waiting",
        note:
          task.current_role === "engineer"
            ? task.status === "CHANGES_REQUESTED"
              ? "Addressing reviewer feedback"
              : "Implementing approved changes"
            : task.implementation_summary
              ? "Implementation delivered"
              : "Waiting for architecture",
      },
      {
        key: "reviewer",
        label: ROLE_LABELS.reviewer,
        state: task.current_role === "reviewer" ? "active" : task.review_result ? "done" : "waiting",
        note: task.current_role === "reviewer" ? "Reviewing the implementation" : task.review_result ? "Review delivered" : "Waiting for implementation",
      },
    );
  }

  return (
    <div className="role-status">
      {rows.map((row) => (
        <div key={row.key} className={`role-status-row ${row.state}`}>
          <span className="dot" aria-hidden>
            {row.state === "active" ? "●" : row.state === "done" ? "✓" : "○"}
          </span>
          <div>
            <div className="role-name">{row.label}</div>
            <div className="muted small">{terminal && row.state !== "done" ? "—" : row.note}</div>
          </div>
        </div>
      ))}
    </div>
  );
}
