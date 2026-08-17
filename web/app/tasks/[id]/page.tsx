"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { Diff, Task } from "@/lib/types";
import ActionBar from "@/components/ActionBar";
import DiffView from "@/components/DiffView";
import EventLog from "@/components/EventLog";
import Markdown from "@/components/Markdown";
import StatusBadge from "@/components/StatusBadge";
import LoadingShell from "@/components/LoadingShell";

const STATUS_META: Record<string, { label: string; phase: number; active: boolean; needsHuman: boolean }> = {
  NEW: { label: "New", phase: 0, active: false, needsHuman: false },
  ARCHITECTURE_ANALYSIS: { label: "Architect analyzing", phase: 0, active: true, needsHuman: false },
  AWAITING_ARCHITECTURE_APPROVAL: { label: "Awaiting approval", phase: 0, active: false, needsHuman: true },
  READY_TO_IMPLEMENT: { label: "Ready to implement", phase: 1, active: false, needsHuman: false },
  IMPLEMENTING: { label: "Engineer implementing", phase: 1, active: true, needsHuman: false },
  TESTING: { label: "Testing", phase: 1, active: false, needsHuman: false },
  REVIEWING: { label: "Reviewer inspecting", phase: 2, active: true, needsHuman: false },
  CHANGES_REQUESTED: { label: "Changes requested", phase: 2, active: false, needsHuman: false },
  READY_FOR_HUMAN: { label: "Ready for human", phase: 3, active: false, needsHuman: true },
  ACCEPTED: { label: "Accepted", phase: 3, active: false, needsHuman: false },
  REJECTED: { label: "Rejected", phase: 3, active: false, needsHuman: false },
  FAILED: { label: "Failed", phase: -1, active: false, needsHuman: false },
  CANCELLED: { label: "Cancelled", phase: -1, active: false, needsHuman: false },
};

export default function TaskDetailPage() {
  const { id } = useParams<{ id: string }>();
  const taskId = Number(id);
  const [task, setTask] = useState<Task | null>(null);
  const [diff, setDiff] = useState<Diff | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [pendingAction, setPendingAction] = useState<string | null>(null);
  const diffKey = useRef<string | null>(null);

  const refresh = useCallback(async () => {
    const t = await api.task(taskId);
    setTask(t);
    if (t.worktree_path || t.result_commit) {
      const nextDiffKey = `${t.worktree_path ?? ""}:${t.result_commit ?? ""}`;
      if (diffKey.current !== nextDiffKey) {
        diffKey.current = nextDiffKey;
        api.taskDiff(taskId).then(setDiff).catch(() => undefined);
      }
    } else {
      diffKey.current = null;
      setDiff(null);
    }
  }, [taskId]);

  const handleEvent = useCallback((event: { type: string }) => {
    if (event.type === "task.transitioned" || event.type.startsWith("workflow.")) {
      refresh().catch(() => undefined);
    }
  }, [refresh]);

  useEffect(() => {
    refresh().catch((e) => setError(String(e)));
  }, [refresh]);

  useEffect(() => {
    if (!task || !["ARCHITECTURE_ANALYSIS", "IMPLEMENTING", "TESTING", "REVIEWING", "CHANGES_REQUESTED"].includes(task.status)) {
      return;
    }
    const timer = setInterval(() => {
      if (document.visibilityState === "visible") refresh().catch(() => undefined);
    }, 10000);
    return () => clearInterval(timer);
  }, [refresh, task?.status]);

  async function onAction(action: string, body?: Record<string, string>) {
    setBusy(true);
    setPendingAction(action);
    setError(null);
    try {
      const authoritative = await api.taskAction(taskId, action, body);
      setTask(authoritative);
      diffKey.current = null;
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
      setPendingAction(null);
    }
  }

  if (!task) return <LoadingShell title="Task" />;

  const meta = STATUS_META[task.status] || { label: task.status, phase: -1, active: false, needsHuman: false };
  const isTerminal = ["ACCEPTED", "REJECTED", "CANCELLED", "FAILED"].includes(task.status);

  return (
    <div>
      <p className="small">
        <Link href="/tasks">← Tasks</Link> ·{" "}
        <Link href={`/projects/${task.project_id}`}>{task.project_name}</Link>
        {task.current_execution_id && (
          <> · <Link href={`/executions/${task.current_execution_id}`}>execution</Link></>
        )}
      </p>

      <div className="row space-between">
        <h1>{task.title}</h1>
        <StatusBadge status={task.status} />
      </div>
      <p className="muted small">
        Priority {task.priority} · created {task.created_at?.slice(0, 16).replace("T", " ")}
        {task.current_role && <> · role: <span className="badge role">{task.current_role}</span></>}
        {task.execution_status && <> · execution: <span className="badge">{task.execution_status.toLowerCase()}</span></>}
      </p>

      {error && <div className="notice error">{error}</div>}

      {/* --- Workflow progress --- */}
      <div className="panel">
        <h3>Workflow progress</h3>
        <div className="stepper" style={{ gap: 12 }}>
          {[
            { key: "architect", label: "Architect", done: !!task.architecture_result, active: task.status === "ARCHITECTURE_ANALYSIS" || task.status === "AWAITING_ARCHITECTURE_APPROVAL" },
            { key: "engineer", label: "Engineer", done: !!task.implementation_summary, active: task.status === "IMPLEMENTING" || task.status === "TESTING" },
            { key: "reviewer", label: "Reviewer / QA", done: !!task.review_result, active: task.status === "REVIEWING" || task.status === "CHANGES_REQUESTED" },
            { key: "human", label: "Human decision", done: isTerminal, active: task.status === "READY_FOR_HUMAN" },
          ].map((phase, i) => (
            <div key={phase.key} className="row" style={{ gap: 6, alignItems: "center" }}>
              {i > 0 && <span className="muted">→</span>}
              <span
                className={`step ${
                  phase.done ? "done" : ""
                } ${phase.active ? "current" : ""}`}
                style={{
                  fontWeight: phase.active ? 700 : 400,
                  opacity: phase.done || phase.active ? 1 : 0.5,
                }}
              >
                {phase.done ? `✓ ${phase.label}` : phase.label}
              </span>
            </div>
          ))}
        </div>
        {meta.needsHuman && (
          <div className="notice warning" style={{ marginTop: 12 }}>
            ⚠ Requires your approval — SceneWorks is waiting for a human decision.
          </div>
        )}
        {task.status === "FAILED" && (
          <div className="notice error" style={{ marginTop: 12 }}>
            This task failed. Inspect the execution logs below, then retry — worktrees and partial work are preserved.
          </div>
        )}
        {task.status === "CANCELLED" && (
          <div className="notice" style={{ marginTop: 12 }}>
            Task was cancelled. All running agents were terminated.
          </div>
        )}
      </div>

      {/* --- Description --- */}
      <div className="panel">
        <h3>Description</h3>
        <p style={{ whiteSpace: "pre-wrap", margin: 0 }}>{task.description || "(none)"}</p>
      </div>

      {/* --- Results & Events --- */}
      <div className="panel">
        <div className="split-2">
          <div>
            <EventLog taskId={taskId} onEvent={handleEvent} />
          </div>
          <div>
            <h3>Results</h3>
            {task.architecture_result ? (
              <div className="result-box">
                <strong style={{ display: "block", marginBottom: 6 }}>
                  Architecture analysis {task.status === "AWAITING_ARCHITECTURE_APPROVAL" && "(needs approval)"}
                </strong>
                <Markdown text={task.architecture_result} />
              </div>
            ) : null}
            {task.implementation_summary ? (
              <div className="result-box" style={{ marginTop: 10 }}>
                <strong style={{ display: "block", marginBottom: 6 }}>Implementation</strong>
                <Markdown text={task.implementation_summary} />
              </div>
            ) : null}
            {task.review_result ? (
              <div className="result-box" style={{ marginTop: 10 }}>
                <strong style={{ display: "block", marginBottom: 6 }}>
                  Review: {task.review_result?.startsWith("APPROVED") ? "✅ Approved" : task.review_result?.startsWith("CHANGES_REQUESTED") ? "🔄 Changes requested" : "Review result"}
                </strong>
                <Markdown text={task.review_result} />
              </div>
            ) : null}
            {!task.architecture_result && !task.implementation_summary && !task.review_result && (
              <div className="empty">Nothing produced yet. Start the architecture to begin.</div>
            )}
            {task.result_commit && (
              <p className="small muted" style={{ marginTop: 10 }}>
                Result commit: <code>{task.result_commit.slice(0, 12)}</code> on{" "}
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

      {/* --- Diff --- */}
      <div className="panel">
        <div className="row space-between">
          <h3>Git diff (base → result)</h3>
          <div className="row" style={{ gap: 8 }}>
            <button className="btn small" onClick={() => refresh()}>
              Refresh
            </button>
          </div>
        </div>
        {diff?.commits?.length ? (
          <p className="small muted" style={{ marginBottom: 8 }}>
            Commits: {diff.commits.map((c) => `${c.sha.slice(0, 7)} ${c.subject}`).join(" · ")}
          </p>
        ) : null}
        <DiffView diff={diff} />
      </div>

      {/* --- Actions --- */}
      <div className="panel">
        <h3>Actions</h3>
        {pendingAction && (
          <div className="notice" role="status" aria-live="polite">
            Action in progress: {pendingAction.replace(/_/g, " ")}. Waiting for backend acknowledgement…
          </div>
        )}
        <ActionBar
          taskId={taskId}
          allowedActions={task.allowed_actions}
          onAction={onAction}
          busy={busy}
          pendingAction={pendingAction}
        />
        <p className="small muted" style={{ marginTop: 8 }}>
          SceneWorks never merges code automatically. Only the founder approves — you decide what to integrate.
        </p>
      </div>
    </div>
  );
}
