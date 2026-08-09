"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Diff, Task } from "@/lib/types";
import ActionBar from "@/components/ActionBar";
import DiffView from "@/components/DiffView";
import EventLog from "@/components/EventLog";
import Markdown from "@/components/Markdown";
import StatusBadge from "@/components/StatusBadge";

const PHASES = ["architect", "engineer", "reviewer", "human"];

const PHASE_LABELS: Record<string, string> = {
  architect: "Architect",
  engineer: "Engineer",
  reviewer: "Reviewer / QA",
  human: "Human",
};

function phaseFor(task: Task | null): number {
  if (!task) return 0;
  if (task.status === "NEW" || task.status === "ARCHITECTURE_ANALYSIS" || task.status === "AWAITING_ARCHITECTURE_APPROVAL")
    return task.status === "AWAITING_ARCHITECTURE_APPROVAL" ? 1 : 0;
  if (["READY_TO_IMPLEMENT", "IMPLEMENTING", "TESTING", "CHANGES_REQUESTED"].includes(task.status)) return 2;
  if (["REVIEWING"].includes(task.status)) return 2;
  return 3;
}

export default function TaskDetailPage() {
  const { id } = useParams<{ id: string }>();
  const taskId = Number(id);
  const [task, setTask] = useState<Task | null>(null);
  const [diff, setDiff] = useState<Diff | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showDebug, setShowDebug] = useState(false);

  const refresh = useCallback(async () => {
    const t = await api.task(taskId);
    setTask(t);
    if (t.worktree_path || t.result_commit) {
      api.taskDiff(taskId).then(setDiff).catch(() => undefined);
    }
  }, [taskId]);

  useEffect(() => {
    refresh().catch((e) => setError(String(e)));
    const timer = setInterval(() => refresh().catch(() => undefined), 4000);
    return () => clearInterval(timer);
  }, [refresh]);

  async function onAction(action: string, body?: Record<string, string>) {
    setBusy(true);
    setError(null);
    try {
      await api.taskAction(taskId, action, body);
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  if (!task) return <div className="empty">Loading…</div>;

  const phase = phaseFor(task);
  const isDone = ["ACCEPTED", "REJECTED", "CANCELLED", "FAILED"].includes(task.status);

  return (
    <div>
      <p className="small">
        <Link href="/tasks">← Tasks</Link> · <Link href={`/projects/${task.project_id}`}>{task.project_name}</Link>
      </p>

      <div className="row space-between">
        <h1>{task.title}</h1>
        <StatusBadge status={task.status} />
      </div>
      <p className="muted small">
        Priority {task.priority} · created {task.created_at?.slice(0, 16).replace("T", " ")}
        {task.current_execution_id && task.execution_status && (
          <> · execution {task.execution_status.toLowerCase()}</>
        )}
      </p>

      {error && <div className="notice error">{error}</div>}

      {task.status === "FAILED" && (
        <div className="notice error">
          This task failed. Inspect the execution logs below, then retry — the worktree and any partial work are preserved.
        </div>
      )}

      <div className="panel">
        <h3>Description</h3>
        <p style={{ whiteSpace: "pre-wrap", margin: 0 }}>{task.description || "(none)"}</p>
      </div>

      <div className="panel">
        <h3>Workflow</h3>
        <div className="stepper">
          {PHASES.map((phaseKey, i) => (
            <div key={phaseKey} className="row" style={{ gap: 6 }}>
              {i > 0 && <span className="muted">→</span>}
              <span className={`step ${i < phase ? "done" : ""} ${i === phase && !isDone ? "current" : ""}`}>
                {PHASE_LABELS[phaseKey]}
              </span>
            </div>
          ))}
        </div>
        {task.current_role && (
          <p className="small" style={{ marginTop: 10 }}>
            Current role: <span className="badge role">{task.current_role}</span>
          </p>
        )}
      </div>

      <div className="panel">
        <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) minmax(0,1fr)", gap: 24 }}>
          <div>
            <EventLog taskId={taskId} />
          </div>
          <div>
            <h3>Current result</h3>
            {task.architecture_result ? (
              <div className="result-box">
                <strong style={{ display: "block", marginBottom: 6 }}>Architecture analysis</strong>
                <Markdown text={task.architecture_result} />
              </div>
            ) : null}
            {task.implementation_summary ? (
              <div className="result-box" style={{ marginTop: 10 }}>
                <strong style={{ display: "block", marginBottom: 6 }}>Implementation summary</strong>
                <Markdown text={task.implementation_summary} />
              </div>
            ) : null}
            {task.review_result ? (
              <div className="result-box" style={{ marginTop: 10 }}>
                <strong style={{ display: "block", marginBottom: 6 }}>Review result</strong>
                <Markdown text={task.review_result} />
              </div>
            ) : null}
            {!task.architecture_result && !task.implementation_summary && !task.review_result && (
              <div className="empty">Nothing produced yet.</div>
            )}
            {task.result_commit && (
              <p className="small muted" style={{ marginTop: 10 }}>
                Result commit: <code>{task.result_commit.slice(0, 12)}</code> on branch{" "}
                <code>{task.task_branch}</code>
              </p>
            )}
            {task.worktree_path && (
              <p className="small muted">
                Worktree: <code>{task.worktree_path}</code>
              </p>
            )}
          </div>
        </div>
      </div>

      <div className="panel">
        <div className="row space-between">
          <h3>Git diff (base → result)</h3>
          <div className="row" style={{ gap: 8 }}>
            <button className="btn small" onClick={() => refresh()}>
              Refresh
            </button>
            <button className="btn small" onClick={() => setShowDebug(!showDebug)}>
              {showDebug ? "Hide raw events" : "Debug: raw events"}
            </button>
          </div>
        </div>
        {diff?.commits?.length ? (
          <p className="small muted" style={{ marginBottom: 8 }}>
            Commits: {diff.commits.map((c) => `${c.sha} ${c.subject}`).join(" · ")}
          </p>
        ) : null}
        <DiffView diff={diff} />
      </div>

      {showDebug && (
        <div className="panel">
          <h3>Raw diagnostics (advanced)</h3>
          <EventLog taskId={taskId} />
        </div>
      )}

      <div className="panel">
        <h3>Actions</h3>
        <ActionBar taskId={taskId} allowedActions={task.allowed_actions} onAction={onAction} busy={busy} />
        <p className="small muted" style={{ marginTop: 8 }}>
          The founder is the only approver. SceneWorks never merges code automatically — you decide what to integrate.
        </p>
      </div>
    </div>
  );
}
