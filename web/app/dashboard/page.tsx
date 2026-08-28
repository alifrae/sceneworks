"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api, errorMessage } from "@/lib/api";
import type { Backend, Dashboard } from "@/lib/types";
import StatusBadge from "@/components/StatusBadge";
import { timeAgo } from "@/lib/format";
import LoadingShell from "@/components/LoadingShell";

// Operational statistics view. This used to be the SceneWorks homepage;
// WP-WEB-2 moved the primary landing experience to the "ask the team"
// composer at "/" and relocated this counters/health view here, reachable
// from the sidebar (see docs/wp-web-2-conversation-model.md section A).
export default function DashboardPage() {
  const [data, setData] = useState<Dashboard | null>(null);
  const [backends, setBackends] = useState<Backend[]>([]);
  const [backendBusy, setBackendBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refreshDashboard = useCallback(() => {
    api.dashboard()
      .then((next) => {
        setData(next);
        setError(null);
      })
      .catch((e) => setError(errorMessage(e)));
  }, []);

  const refreshBackends = useCallback(async (force = false) => {
    if (force) setBackendBusy(true);
    try {
      setBackends(await api.backends(force));
    } finally {
      if (force) setBackendBusy(false);
    }
  }, []);

  useEffect(() => {
    refreshDashboard();
    // Provider checks run independently from dashboard data. A forced first
    // refresh returns the actual provider result instead of leaving a cold
    // "probing" snapshot cached in the browser.
    refreshBackends(true).catch(() => undefined);

    const timer = setInterval(() => {
      if (document.visibilityState !== "visible") return;
      refreshDashboard();
      refreshBackends(false).catch(() => undefined);
    }, 10_000);
    return () => clearInterval(timer);
  }, [refreshBackends, refreshDashboard]);

  if (error && !data) return <div className="notice error">Cannot reach the SceneWorks API: {error}</div>;
  if (!data) return <LoadingShell title="Dashboard" />;

  const gemini = backends.find((b) => b.key === "gemini_acp");
  const openhands = backends.find((b) => b.key === "openhands");
  const fake = backends.find((b) => b.key === "fake");
  const liveBackendUp = Boolean(gemini?.available || openhands?.available);
  const linkStyle = { color: "inherit", textDecoration: "none" } as const;

  return (
    <div>
      <div className="row space-between">
        <div>
          <h1>Dashboard</h1>
          <p className="muted">Operational view. Click a metric to inspect the underlying work.</p>
        </div>
        <button className="btn small" onClick={() => refreshDashboard()}>
          Refresh
        </button>
      </div>

      {error && <div className="notice error">Latest refresh failed: {error}</div>}

      <div className="kpi-grid" style={{ margin: "16px 0" }}>
        <Link className="kpi" style={linkStyle} href="/work?filter=active">
          <div className="value">{data.active_tasks}</div>
          <div className="label">Active tasks</div>
        </Link>
        <Link className="kpi" style={linkStyle} href="/work?filter=attention">
          <div className="value">{data.awaiting_approval}</div>
          <div className="label">Awaiting your approval</div>
        </Link>
        <Link className="kpi" style={linkStyle} href="/executions?status=RUNNING">
          <div className="value">{data.running_executions}</div>
          <div className="label">Running agents</div>
        </Link>
        <Link className="kpi" style={linkStyle} href="/executions?status=FAILED">
          <div className="value">{data.failed_executions.length}</div>
          <div className="label">Recent failed executions</div>
        </Link>
      </div>

      <div className="panel">
        <div className="row space-between">
          <h2 style={{ marginBottom: 0 }}>Backend status</h2>
          <div className="row">
            <Link href="/settings" className="btn small">Settings</Link>
            <button
              className="btn small"
              disabled={backendBusy}
              onClick={() => refreshBackends(true).catch(() => undefined)}
            >
              {backendBusy ? "Checking…" : "Check now"}
            </button>
          </div>
        </div>
        <div className="stack sm" style={{ marginTop: 12 }}>
          {backends.map((be) => (
            <Link
              key={be.key}
              href="/settings"
              style={{ ...linkStyle, display: "flex", alignItems: "center", gap: 12 }}
            >
              <span
                className={`badge ${be.available ? "success" : "error"}`}
                style={{ fontSize: 13 }}
              >
                {be.available ? "●" : "○"} {be.label || be.key}
              </span>
              <span className="muted small">{be.detail || be.version || (be.available ? "available" : "unavailable")}</span>
            </Link>
          ))}
          {backends.length === 0 && <span className="muted small">Backend health has not loaded yet.</span>}
          {!liveBackendUp && fake?.available && (
            <div className="notice" style={{ margin: "4px 0 0" }}>
              No live model backend is available. The scripted Fake backend is healthy, but it is for tests and demos only.
            </div>
          )}
          {!liveBackendUp && fake && !fake.available && (
            <div className="notice error" style={{ margin: "4px 0 0" }}>
              No backend is currently available. Open Settings or use “Check now” for the provider diagnostic.
            </div>
          )}
        </div>
      </div>

      <div className="panel">
        <div className="row space-between">
          <h2>Recently completed tasks</h2>
          <Link href="/work?filter=completed" className="btn small">View all</Link>
        </div>
        {data.recently_completed.length === 0 ? (
          <div className="empty">No tasks completed yet.</div>
        ) : (
          <table className="grid">
            <thead>
              <tr>
                <th>Task</th>
                <th>Project</th>
                <th>Status</th>
                <th>Updated</th>
              </tr>
            </thead>
            <tbody>
              {data.recently_completed.map((task) => (
                <tr key={task.id}>
                  <td>
                    <Link href={`/work/${task.id}`}>{task.title}</Link>
                  </td>
                  <td>{task.project_name}</td>
                  <td>
                    <StatusBadge status={task.status} />
                  </td>
                  <td className="muted small">{timeAgo(task.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="panel">
        <div className="row space-between">
          <h2>Failed executions</h2>
          <Link href="/executions?status=FAILED" className="btn small">View all</Link>
        </div>
        {data.failed_executions.length === 0 ? (
          <div className="empty">No failures.</div>
        ) : (
          <table className="grid">
            <thead>
              <tr>
                <th>Role</th>
                <th>Backend</th>
                <th>Error</th>
                <th>Finished</th>
              </tr>
            </thead>
            <tbody>
              {data.failed_executions.map((ex) => (
                <tr key={ex.id}>
                  <td><Link href={`/executions/${ex.id}`}>{ex.role}</Link></td>
                  <td>{ex.backend}</td>
                  <td className="small" style={{ maxWidth: 300, overflow: "hidden", textOverflow: "ellipsis" }}>
                    {ex.error || "—"}
                  </td>
                  <td className="muted small">{ex.finished_at ? timeAgo(ex.finished_at) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="panel">
        <div className="row space-between">
          <h2>Company roles</h2>
          <Link href="/company" className="btn small">Open team</Link>
        </div>
        <div className="row">
          {data.roles.map((role) => (
            <Link key={role.key} href="/company" className="btn small">
              {role.display_name} <span className="muted">· {role.backend}</span>
            </Link>
          ))}
        </div>
      </div>
    </div>
  );
}
