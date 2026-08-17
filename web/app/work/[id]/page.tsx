"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { AppEvent, Diff, Execution, Task } from "@/lib/types";
import { ROLE_LABELS, getWorkView, meaningfulActions } from "@/lib/workStages";
import { firstParagraph, filesChangedCount, reviewVerdictLabel } from "@/lib/textSummary";
import Composer from "@/components/Composer";
import DecisionCard from "@/components/DecisionCard";
import DiffView from "@/components/DiffView";
import EventLog from "@/components/EventLog";
import LoadingShell from "@/components/LoadingShell";
import Markdown from "@/components/Markdown";
import ProgressSteps from "@/components/ProgressSteps";
import RoleStatusPanel from "@/components/RoleStatusPanel";

type TabKey = "plan" | "changes" | "results" | "activity" | "advanced";

const TAB_KEYS: TabKey[] = ["plan", "changes", "results", "activity", "advanced"];

export default function WorkThreadPage() {
  const { id } = useParams<{ id: string }>();
  const taskId = Number(id);
  const [task, setTask] = useState<Task | null>(null);
  const [diff, setDiff] = useState<Diff | null>(null);
  const [taskEvents, setTaskEvents] = useState<AppEvent[]>([]);
  const [executions, setExecutions] = useState<Execution[]>([]);
  const [tab, setTab] = useState<TabKey>("plan");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [pendingAction, setPendingAction] = useState<string | null>(null);
  const [followUp, setFollowUp] = useState(false);
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
    api.taskEvents(taskId).then((rows) => setTaskEvents(rows)).catch(() => undefined);
    return t;
  }, [taskId]);

  const handleEvent = useCallback(
    (event: { type: string }) => {
      if (event.type === "task.transitioned" || event.type.startsWith("workflow.")) {
        refresh().catch(() => undefined);
      }
    },
    [refresh],
  );

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

  useEffect(() => {
    if (tab === "advanced") {
      api.executions({ task_id: String(taskId) }).then(setExecutions).catch(() => undefined);
    }
  }, [tab, taskId]);

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

  if (!task) return <LoadingShell title="Work" />;

  const view = getWorkView(task);
  const notes = taskEvents.filter((e) => e.type === "task.note");
  const isTerminalDone = task.status === "ACCEPTED" || task.status === "REJECTED";
  const isCancelled = task.status === "CANCELLED";
  const meaningful = meaningfulActions(task.allowed_actions);
  // An in-flight execution (including an auto-repair loop that never left
  // CHANGES_REQUESTED) must never offer "start implementation" as a
  // follow-up — the state machine allows it, but clicking it would race a
  // second graph run against the one already active. Only "cancel" is safe
  // to expose while something is actively running.
  const activeExecution = !!task.current_execution_id && ["QUEUED", "STARTING", "RUNNING"].includes(task.execution_status || "");
  const runningNoDecision = view.exceptional === "none" && (activeExecution || meaningful.every((a) => a === "cancel"));

  return (
    <div className="thread">
      <p className="small">
        <Link href="/work">← Work</Link> ·{" "}
        <Link href={`/projects/${task.project_id}`}>{task.project_name}</Link>
      </p>

      <div className="row space-between" style={{ alignItems: "flex-start" }}>
        <h1 className="thread-title">{task.title}</h1>
        <span className={`stage-badge stage-${view.exceptional !== "none" ? view.exceptional : view.stage}`} style={{ flexShrink: 0 }}>
          {view.displayLabel}
        </span>
      </div>

      {error && <div className="notice error">{error}</div>}

      {view.attentionReason && (
        <div className={`notice ${view.exceptional === "failed" || view.exceptional === "blocked" ? "error" : ""}`}>
          {view.attentionReason}
        </div>
      )}
      {isCancelled && <div className="notice">This request was cancelled — no further work will happen.</div>}

      <div className="thread-body">
        <div className="thread-conversation">
          <div className="panel">
            <div className="turn">
              <div className="turn-author">You</div>
              <div className="turn-content">{task.description || task.title}</div>
            </div>

            {task.architecture_result && (
              <div className="turn">
                <div className="turn-author">{ROLE_LABELS.architect}</div>
                <div className="turn-content">
                  {firstParagraph(task.architecture_result)}{" "}
                  <button className="link-btn" onClick={() => setTab("plan")}>
                    View full plan →
                  </button>
                </div>
              </div>
            )}

            {task.implementation_summary && (
              <div className="turn">
                <div className="turn-author">{ROLE_LABELS.engineer}</div>
                <div className="turn-content">
                  {firstParagraph(task.implementation_summary)}{" "}
                  <button className="link-btn" onClick={() => setTab("changes")}>
                    View changes →
                  </button>
                </div>
              </div>
            )}

            {task.review_result && (
              <div className="turn">
                <div className="turn-author">{ROLE_LABELS.reviewer}</div>
                <div className="turn-content">
                  <strong>{reviewVerdictLabel(task.review_result)}.</strong> {firstParagraph(task.review_result)}{" "}
                  <button className="link-btn" onClick={() => setTab("results")}>
                    View results →
                  </button>
                </div>
              </div>
            )}

            {notes.length > 0 && (
              <div className="turn notes">
                <div className="turn-author muted">Notes</div>
                {notes.slice(-5).map((n) => (
                  <div key={n.id} className="note-line muted small">
                    {String(n.payload.title || "")}
                    {n.payload.detail ? `: ${String(n.payload.detail)}` : ""}
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="panel">
            {isTerminalDone || isCancelled ? (
              followUp ? (
                <Composer defaultProjectId={task.project_id} />
              ) : (
                <button className="btn" onClick={() => setFollowUp(true)}>
                  Ask a follow-up
                </button>
              )
            ) : runningNoDecision ? (
              <div className="row space-between">
                <span className="turn-active muted small">
                  <span className="dot" aria-hidden />
                  {view.ownerLabel} is working — SceneWorks will ask you here when your input is needed.
                </span>
                {meaningful.includes("cancel") && (
                  <DecisionCard
                    allowedActions={["cancel"]}
                    busy={busy}
                    pendingAction={pendingAction}
                    onAction={onAction}
                  />
                )}
              </div>
            ) : (
              <DecisionCard allowedActions={task.allowed_actions} busy={busy} pendingAction={pendingAction} onAction={onAction} />
            )}
          </div>
        </div>

        <div className="thread-side">
          <div className="panel">
            <h3>Progress</h3>
            <ProgressSteps steps={view.progress} exceptional={view.exceptional} />
          </div>
          <div className="panel">
            <h3>Team</h3>
            <RoleStatusPanel task={task} />
          </div>
        </div>
      </div>

      <div className="panel thread-tabs">
        <div className="row tab-bar" role="tablist" aria-label="Work Thread sections">
          {TAB_KEYS.map((t) => (
            <button
              key={t}
              role="tab"
              id={`tab-${t}`}
              aria-selected={tab === t}
              aria-controls={`tabpanel-${t}`}
              tabIndex={tab === t ? 0 : -1}
              className={`tab ${tab === t ? "active" : ""}`}
              onClick={() => setTab(t)}
              onKeyDown={(e) => {
                if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
                e.preventDefault();
                const idx = TAB_KEYS.indexOf(t);
                const next = TAB_KEYS[(idx + (e.key === "ArrowRight" ? 1 : TAB_KEYS.length - 1)) % TAB_KEYS.length];
                setTab(next);
                document.getElementById(`tab-${next}`)?.focus();
              }}
            >
              {t[0].toUpperCase() + t.slice(1)}
            </button>
          ))}
        </div>

        {tab === "plan" && (
          <div className="tab-panel" role="tabpanel" id="tabpanel-plan" aria-labelledby="tab-plan">
            {task.architecture_result ? (
              <Markdown text={task.architecture_result} />
            ) : (
              <div className="empty">No architecture plan yet.</div>
            )}
          </div>
        )}

        {tab === "changes" && (
          <div className="tab-panel" role="tabpanel" id="tabpanel-changes" aria-labelledby="tab-changes">
            <p className="small muted">
              Analyzed commit: <code>{task.base_commit?.slice(0, 12) || "—"}</code>
              {task.task_branch && (
                <>
                  {" "}
                  · Implementation branch: <code>{task.task_branch}</code>
                </>
              )}
              {task.result_commit && (
                <>
                  {" "}
                  · Result commit: <code>{task.result_commit.slice(0, 12)}</code>
                </>
              )}
            </p>
            {diff?.commits?.length ? (
              <p className="small muted">
                Commits: {diff.commits.map((c) => `${c.sha.slice(0, 7)} ${c.subject}`).join(" · ")}
              </p>
            ) : null}
            <DiffView diff={diff} />
          </div>
        )}

        {tab === "results" && (
          <div className="tab-panel" role="tabpanel" id="tabpanel-results" aria-labelledby="tab-results">
            {!isTerminalDone && task.status !== "READY_FOR_HUMAN" ? (
              <div className="empty">Not finished yet — check back once the reviewer has weighed in.</div>
            ) : (
              <div className="result-summary">
                {task.review_result && (
                  <div className="result-outcome">
                    <span
                      className={`stage-badge ${
                        reviewVerdictLabel(task.review_result) === "Approved" ? "stage-completed" : "stage-needs_input"
                      }`}
                    >
                      {reviewVerdictLabel(task.review_result)}
                    </span>
                    <span className="muted small">Reviewer verdict</span>
                  </div>
                )}

                {(diff?.commits?.length || task.implementation_summary) && (
                  <div>
                    <div className="result-section-title">Summary</div>
                    {diff?.commits?.length ? (
                      <ul>
                        {diff.commits.map((c) => (
                          <li key={c.sha}>{c.subject}</li>
                        ))}
                      </ul>
                    ) : (
                      <Markdown text={firstParagraph(task.implementation_summary!, 800)} />
                    )}
                  </div>
                )}

                <div className="row result-meta">
                  {task.result_commit && (
                    <span>
                      Commit <code>{task.result_commit.slice(0, 12)}</code>
                    </span>
                  )}
                  {diff?.stat && filesChangedCount(diff.stat) !== null && (
                    <span>Files changed: {filesChangedCount(diff.stat)}</span>
                  )}
                </div>

                {task.review_result && (
                  <div>
                    <div className="result-section-title">Reviewer notes</div>
                    <Markdown text={task.review_result} />
                  </div>
                )}

                <div className="row">
                  <button className="btn small" onClick={() => setTab("changes")}>
                    View changes
                  </button>
                  <button className="btn small" onClick={() => setTab("plan")}>
                    View plan
                  </button>
                  <button className="btn small" onClick={() => setTab("activity")}>
                    Open activity
                  </button>
                </div>
              </div>
            )}
          </div>
        )}

        <div
          className="tab-panel"
          role="tabpanel"
          id="tabpanel-activity"
          aria-labelledby="tab-activity"
          style={{ display: tab === "activity" ? "block" : "none" }}
        >
          <EventLog taskId={taskId} onEvent={handleEvent} />
        </div>

        {tab === "advanced" && (
          <div className="tab-panel" role="tabpanel" id="tabpanel-advanced" aria-labelledby="tab-advanced">
            <div className="advanced-group">
              <div className="advanced-group-title">Task</div>
              <table className="grid">
                <tbody>
                  <tr>
                    <td className="muted">Task ID</td>
                    <td className="mono">{task.id}</td>
                  </tr>
                  <tr>
                    <td className="muted">Raw status</td>
                    <td className="mono">{task.status}</td>
                  </tr>
                </tbody>
              </table>
            </div>

            <div className="advanced-group">
              <div className="advanced-group-title">Execution</div>
              <table className="grid">
                <tbody>
                  <tr>
                    <td className="muted">Current execution</td>
                    <td className="mono">{task.current_execution_id || "—"}</td>
                  </tr>
                </tbody>
              </table>
              {executions.length === 0 ? (
                <div className="empty">No executions recorded.</div>
              ) : (
                <table className="grid" style={{ marginTop: 8 }}>
                  <thead>
                    <tr>
                      <th>Role</th>
                      <th>Backend</th>
                      <th>Status</th>
                      <th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {executions.map((ex) => (
                      <tr key={ex.id}>
                        <td>{ex.role}</td>
                        <td className="mono small">{ex.backend}</td>
                        <td><span className="status-chip">{ex.status}</span></td>
                        <td>
                          <Link href={`/executions/${ex.id}`}>details</Link>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>

            <div className="advanced-group">
              <div className="advanced-group-title">Git / worktree</div>
              <table className="grid">
                <tbody>
                  <tr>
                    <td className="muted">Worktree</td>
                    <td className="mono">{task.worktree_path || "—"}</td>
                  </tr>
                  <tr>
                    <td className="muted">Base commit</td>
                    <td className="mono">{task.base_commit || "—"}</td>
                  </tr>
                  <tr>
                    <td className="muted">Task branch</td>
                    <td className="mono">{task.task_branch || "—"}</td>
                  </tr>
                </tbody>
              </table>
            </div>

            <div className="advanced-group">
              <div className="advanced-group-title">Links</div>
              <p className="small muted">
                <Link href={`/tasks/${task.id}`}>Open raw task view →</Link>
              </p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
