"use client";

import Link from "next/link";
import type { Task } from "@/lib/types";
import { getWorkView } from "@/lib/workStages";
import { timeAgo } from "@/lib/format";

export default function WorkRow({ task, reason }: { task: Task; reason?: string | null }) {
  const view = getWorkView(task);
  return (
    <Link href={`/work/${task.id}`} className={`work-row ${view.exceptional !== "none" ? `x-${view.exceptional}` : ""}`}>
      <div className="work-row-main">
        <div className="work-row-title" title={task.title}>{task.title}</div>
        <div className="muted small">
          {task.project_name}
          {reason ? ` · ${reason}` : view.ownerRole ? ` · ${view.ownerLabel} working` : ""}
        </div>
      </div>
      <div className="work-row-meta">
        <span className={`stage-badge stage-${view.exceptional !== "none" ? view.exceptional : view.stage}`}>
          {view.displayLabel}
        </span>
        <span className="muted small">{timeAgo(task.updated_at)}</span>
      </div>
    </Link>
  );
}
