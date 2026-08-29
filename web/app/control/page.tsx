"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { API_URL } from "@/lib/api";

type ControlCenter = {
  projects: number;
  tasks: { active: number; needs_attention: number };
  issues: { open: number; closed: number };
  engineering_sessions: Array<{
    id: number;
    project_id: number;
    project_name: string;
    task_id: number | null;
    task_title: string | null;
    runtime: string;
    status: string;
    branch: string | null;
    permissions: string[];
    updated_at: string | null;
  }>;
  pcs_runs: Array<{
    id: number;
    project_id: number;
    project_name: string;
    engineering_session_id: number;
    task_id: number | null;
    task_title: string | null;
    profile: string;
    status: string;
    pid: number | null;
    exit_code: number | null;
    updated_at: string | null;
  }>;
  recent_evidence: Array<{
    id: number;
    engineering_session_id: number;
    project_name: string;
    task_id: number | null;
    task_title: string | null;
    turn_id: string | null;
    action_id: string;
    category: string;
    operation: string;
    status: string;
    created_at: string | null;
  }>;
};

function shortTime(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export default function ControlPage() {
  const [snapshot, setSnapshot] = useState<ControlCenter | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const response = await fetch(`${API_URL}/api/control-center`, { cache: "no-store" });
        if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
        const body = (await response.json()) as ControlCenter;
        if (!cancelled) {
          setSnapshot(body);
          setError(null);
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      }
    };
    load();
    const timer = window.setInterval(load, 5000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  return (
    <div>
      <div className="page-heading-row">
        <div>
          <h1>Control</h1>
          <p className="muted">Current engineering sessions, PCS processes and evidence. This page observes the runtime; governed mutations remain in task and MCP flows.</p>
        </div>
        <div className="small muted">refreshes every 5 s</div>
      </div>

      {error && <div className="notice error">Control-center API unavailable: {error}</div>}

      <div className="control-kpis">
        <div className="kpi"><span>Projects</span><strong>{snapshot?.projects ?? "—"}</strong></div>
        <div className="kpi"><span>Active work</span><strong>{snapshot?.tasks.active ?? "—"}</strong></div>
        <div className="kpi"><span>Needs attention</span><strong>{snapshot?.tasks.needs_attention ?? "—"}</strong></div>
        <div className="kpi"><span>Open issues</span><strong>{snapshot?.issues.open ?? "—"}</strong></div>
      </div>

      <section className="section">
        <div className="section-head">
          <h3>Engineering sessions</h3>
          <Link href="/work" className="small">Work →</Link>
        </div>
        <div className="panel flush-panel">
          {!snapshot ? (
            <div className="empty">Loading runtime…</div>
          ) : snapshot.engineering_sessions.length === 0 ? (
            <div className="empty">No engineering sessions yet.</div>
          ) : (
            <div className="control-list">
              {snapshot.engineering_sessions.map((row) => (
                <div className="control-row" key={row.id}>
                  <div>
                    <div className="control-title">
                      {row.task_id ? <Link href={`/work/${row.task_id}`}>{row.task_title ?? `Task ${row.task_id}`}</Link> : "Unbound engineering session"}
                    </div>
                    <div className="small muted">{row.project_name} · session {row.id} · {row.runtime}{row.branch ? ` · ${row.branch}` : ""}</div>
                  </div>
                  <div className="control-row-right">
                    <span className={`runtime-state ${row.status.toLowerCase()}`}>{row.status}</span>
                    <span className="small muted">{shortTime(row.updated_at)}</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </section>

      <section className="section">
        <div className="section-head"><h3>Managed PCS</h3></div>
        <div className="panel flush-panel">
          {!snapshot ? (
            <div className="empty">Loading PCS state…</div>
          ) : snapshot.pcs_runs.length === 0 ? (
            <div className="empty">No managed PCS runs.</div>
          ) : (
            <div className="control-list">
              {snapshot.pcs_runs.map((row) => (
                <div className="control-row" key={row.id}>
                  <div>
                    <div className="control-title">
                      {row.task_id ? <Link href={`/work/${row.task_id}`}>{row.task_title ?? `Task ${row.task_id}`}</Link> : row.project_name}
                    </div>
                    <div className="small muted">{row.project_name} · profile {row.profile} · session {row.engineering_session_id}{row.pid ? ` · PID ${row.pid}` : ""}</div>
                  </div>
                  <div className="control-row-right">
                    <span className={`runtime-state ${row.status.toLowerCase()}`}>{row.status}</span>
                    <span className="small muted">{row.exit_code === null ? shortTime(row.updated_at) : `exit ${row.exit_code}`}</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </section>

      <section className="section">
        <div className="section-head"><h3>Recent evidence</h3></div>
        <div className="panel flush-panel">
          {!snapshot ? (
            <div className="empty">Loading evidence…</div>
          ) : snapshot.recent_evidence.length === 0 ? (
            <div className="empty">No engineering evidence yet.</div>
          ) : (
            <div className="evidence-list">
              {snapshot.recent_evidence.map((row) => (
                <div className="evidence-row" key={row.id}>
                  <span className={`runtime-state ${row.status.toLowerCase()}`}>{row.status}</span>
                  <div>
                    <div className="mono small">{row.operation}</div>
                    <div className="small muted">{row.project_name}{row.task_id ? ` · ${row.task_title ?? `Task ${row.task_id}`}` : ""} · {row.category}</div>
                  </div>
                  <span className="small muted evidence-time">{shortTime(row.created_at)}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
