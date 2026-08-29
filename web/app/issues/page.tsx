"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { api, errorMessage } from "@/lib/api";
import { updateBacklogTask } from "@/lib/taskManagement";
import type { Project, Task, WorkItemType } from "@/lib/types";
import { useTasks } from "@/lib/useTasks";

const ISSUE_TYPES: WorkItemType[] = ["bug", "feature", "idea"];
const CLOSED = new Set(["ACCEPTED", "REJECTED", "CANCELLED"]);

export default function IssuesPage() {
  const { tasks, error: taskError } = useTasks();
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState("");
  const [title, setTitle] = useState("");
  const [type, setType] = useState<WorkItemType>("bug");
  const [priority, setPriority] = useState<"low" | "medium" | "high">("medium");
  const [view, setView] = useState<"open" | "closed" | "all">("open");
  const [busy, setBusy] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);

  useEffect(() => {
    api.projects()
      .then((rows) => {
        setProjects(rows);
        if (!projectId && rows[0]) setProjectId(String(rows[0].id));
      })
      .catch((e) => setLocalError(errorMessage(e)));
  }, [projectId]);

  const issues = useMemo(() => {
    const rows = (tasks ?? []).filter((task) => ISSUE_TYPES.includes(task.work_item_type));
    return rows.filter((task) => {
      if (view === "all") return true;
      const closed = CLOSED.has(task.status);
      return view === "closed" ? closed : !closed;
    });
  }, [tasks, view]);

  const openCount = (tasks ?? []).filter(
    (task) => ISSUE_TYPES.includes(task.work_item_type) && !CLOSED.has(task.status),
  ).length;
  const closedCount = (tasks ?? []).filter(
    (task) => ISSUE_TYPES.includes(task.work_item_type) && CLOSED.has(task.status),
  ).length;

  async function createIssue() {
    if (!title.trim() || !projectId) return;
    setBusy(true);
    setLocalError(null);
    try {
      const task = await api.createTask({
        project_id: Number(projectId),
        title: title.trim().slice(0, 120),
        description: title.trim(),
        work_item_type: type,
        priority,
        requested_mode: "auto",
      });
      setTitle("");
      window.location.href = `/work/${task.id}`;
    } catch (e) {
      setLocalError(errorMessage(e));
      setBusy(false);
    }
  }

  async function patchIssue(task: Task, patch: { priority?: "low" | "medium" | "high"; work_item_type?: WorkItemType }) {
    setLocalError(null);
    try {
      await updateBacklogTask(task.id, patch);
      window.location.reload();
    } catch (e) {
      setLocalError(errorMessage(e));
    }
  }

  return (
    <div>
      <div className="page-heading-row">
        <div>
          <h1>Issues</h1>
          <p className="muted">A small engineering issue list. Bugs, features and ideas use the same SceneWorks task lifecycle—no separate board or ticket system.</p>
        </div>
        <div className="issue-counts">
          <span><strong>{openCount}</strong> open</span>
          <span><strong>{closedCount}</strong> closed</span>
        </div>
      </div>

      {(taskError || localError) && <div className="notice error">{localError ?? taskError}</div>}

      <div className="panel issue-capture">
        <div className="issue-capture-main">
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Capture a bug, feature or idea…"
            onKeyDown={(e) => {
              if (e.key === "Enter") createIssue();
            }}
            disabled={busy}
          />
        </div>
        <select value={type} onChange={(e) => setType(e.target.value as WorkItemType)} disabled={busy}>
          <option value="bug">Bug</option>
          <option value="feature">Feature</option>
          <option value="idea">Idea</option>
        </select>
        <select value={priority} onChange={(e) => setPriority(e.target.value as "low" | "medium" | "high")} disabled={busy}>
          <option value="low">Low</option>
          <option value="medium">Medium</option>
          <option value="high">High</option>
        </select>
        {projects.length > 1 && (
          <select value={projectId} onChange={(e) => setProjectId(e.target.value)} disabled={busy}>
            {projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}
          </select>
        )}
        <button className="btn primary" onClick={createIssue} disabled={busy || !title.trim() || !projectId}>
          {busy ? "Adding…" : "Add"}
        </button>
      </div>

      <div className="segmented" aria-label="Issue state filter">
        <button className={view === "open" ? "active" : ""} onClick={() => setView("open")}>Open</button>
        <button className={view === "closed" ? "active" : ""} onClick={() => setView("closed")}>Closed</button>
        <button className={view === "all" ? "active" : ""} onClick={() => setView("all")}>All</button>
      </div>

      <div className="panel issue-list-panel">
        {tasks === null ? (
          <div className="empty">Loading issues…</div>
        ) : issues.length === 0 ? (
          <div className="empty">No {view === "all" ? "" : `${view} `}issues.</div>
        ) : (
          <div className="issue-list">
            {issues.map((task) => (
              <div className="issue-row" key={task.id}>
                <div className="issue-main">
                  <div className="issue-meta">
                    <span className={`issue-kind ${task.work_item_type}`}>{task.work_item_type}</span>
                    <span>{task.project_name}</span>
                    <span>#{task.id}</span>
                  </div>
                  <Link className="issue-title" href={`/work/${task.id}`}>{task.title}</Link>
                  <div className="small muted">{task.status.replaceAll("_", " ").toLowerCase()}</div>
                </div>
                <div className="issue-actions">
                  <select
                    aria-label={`Type for ${task.title}`}
                    value={task.work_item_type}
                    onChange={(e) => patchIssue(task, { work_item_type: e.target.value as WorkItemType })}
                  >
                    <option value="bug">Bug</option>
                    <option value="feature">Feature</option>
                    <option value="idea">Idea</option>
                  </select>
                  <select
                    aria-label={`Priority for ${task.title}`}
                    value={task.priority}
                    onChange={(e) => patchIssue(task, { priority: e.target.value as "low" | "medium" | "high" })}
                  >
                    <option value="low">Low</option>
                    <option value="medium">Medium</option>
                    <option value="high">High</option>
                  </select>
                  <Link className="btn small" href={`/work/${task.id}`}>Open</Link>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
