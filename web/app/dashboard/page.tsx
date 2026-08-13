"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
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
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // Resolved independently: a backend health probe shells out to the agent
    // CLI and can take seconds, and the dashboard must not wait on it to
    // render.
    api.dashboard().then(setData).catch((e) => setError(String(e)));
    api.backends().then((b) => setBackends(b as Backend[])).catch(() => undefined);

    const timer = setInterval(() => {
      api.dashboard().then(setData).catch(() => undefined);
      api.backends().then((b) => setBackends(b as Backend[])).catch(() => undefined);
    }, 10_000);
    return () => clearInterval(timer);
  }, []);

  if (error) return <div className="notice error">Cannot reach the SceneWorks API: {error}</div>;
  if (!data) return <LoadingShell title="Dashboard" />;

  const geminiUp = backends.find((b) => b.key === "gemini_acp")?.available ?? false;
  const openhandsUp = backends.find((b) => b.key === "openhands")?.available ?? false;

  return (
    <div>
      <h1>Dashboard</h1>
      <p className="muted">Operational view. Agents run on the worker; nothing here is simulated.</p>

      <div className="kpi-grid" style={{ margin: "16px 0" }}>
        <div className="kpi">
          <div className="value">{data.active_tasks}</div>
          <div className="label">Active tasks</div>
        </div>
        <div className="kpi">
          <div className="value">{data.awaiting_approval}</div>
          <div className="label">Awaiting your approval</div>
        </div>
        <div className="kpi">
          <div className="value">{data.running_executions}</div>
          <div className="label">Running agents</div>
        </div>
        <div className="kpi">
          <div className="value">{data.failed_executions.length}</div>
          <div className="label">Recent failed executions</div>
        </div>
      </div>

      <div className="panel">
        <h2>Backend status</h2>
        <div className="row" style={{ gap: 12, marginTop: 8 }}>
          {backends.map((be) => (
            <span
              key={be.key}
              className={`badge ${be.available ? "success" : "error"}`}
              title={be.detail || ""}
              style={{ fontSize: 13 }}
            >
              {be.available ? "●" : "○"} {be.label || be.key}
            </span>
          ))}
          {!geminiUp && !openhandsUp && (
            <span className="muted small" style={{ marginLeft: 4 }}>
              No live backends. Using fake/simulated agents (development mode).
            </span>
          )}
        </div>
      </div>

      <div className="panel">
        <h2>Recently completed tasks</h2>
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
        <h2>Failed executions</h2>
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
                  <td>{ex.role}</td>
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
        <h2>Company roles</h2>
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
