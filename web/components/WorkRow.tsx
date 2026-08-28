"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import type { ExecutionMode, Task, WorkItemType } from "@/lib/types";
import { getWorkView } from "@/lib/workStages";
import { timeAgo } from "@/lib/format";
import { updateBacklogTask } from "@/lib/taskManagement";

const TYPE_LABELS: Record<WorkItemType, string> = {
  task: "Task",
  bug: "Bug",
  feature: "Feature",
  idea: "Idea",
};

function modeLabel(task: Task): string {
  const mode = task.resolved_mode ?? task.requested_mode;
  return mode[0].toUpperCase() + mode.slice(1);
}

export default function WorkRow({ task: initialTask, reason }: { task: Task; reason?: string | null }) {
  const [task, setTask] = useState(initialTask);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => setTask(initialTask), [initialTask]);

  const view = getWorkView(task);

  async function patch(body: { work_item_type?: WorkItemType; requested_mode?: ExecutionMode; priority?: "low" | "medium" | "high" }) {
    setBusy(true);
    setError(null);
    try {
      setTask(await updateBacklogTask(task.id, body));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={`work-row ${view.exceptional !== "none" ? `x-${view.exceptional}` : ""}`}>
      <Link href={`/work/${task.id}`} className="work-row-main" style={{ textDecoration: "none", color: "inherit" }}>
        <div className="work-row-title" title={task.title}>{task.title}</div>
        <div className="muted small">
          {TYPE_LABELS[task.work_item_type]} · {modeLabel(task)} · {task.project_name}
          {reason ? ` · ${reason}` : view.ownerRole ? ` · ${view.ownerLabel} working` : ""}
          {error ? ` · ${error}` : ""}
        </div>
      </Link>
      <div className="work-row-meta">
        {task.status === "NEW" ? (
          <div className="row" style={{ gap: 6, flexWrap: "wrap", justifyContent: "flex-end" }}>
            <select
              aria-label={`Type for ${task.title}`}
              value={task.work_item_type}
              disabled={busy}
              onChange={(e) => patch({ work_item_type: e.target.value as WorkItemType })}
            >
              <option value="task">Task</option>
              <option value="bug">Bug</option>
              <option value="feature">Feature</option>
              <option value="idea">Idea</option>
            </select>
            <select
              aria-label={`Mode for ${task.title}`}
              value={task.requested_mode}
              disabled={busy}
              onChange={(e) => patch({ requested_mode: e.target.value as ExecutionMode })}
            >
              <option value="auto">Auto</option>
              <option value="change">Change</option>
              <option value="investigate">Investigate</option>
              <option value="plan">Plan</option>
              <option value="ask">Ask</option>
            </select>
          </div>
        ) : (
          <span className={`stage-badge stage-${view.exceptional !== "none" ? view.exceptional : view.stage}`}>
            {view.displayLabel}
          </span>
        )}
        <span className="muted small">{task.status === "NEW" ? "Backlog" : timeAgo(task.updated_at)}</span>
      </div>
    </div>
  );
}
