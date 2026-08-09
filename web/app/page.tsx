"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Dashboard } from "@/lib/types";
import StatusBadge from "@/components/StatusBadge";
import { timeAgo } from "@/lib/format";

export default function DashboardPage() {
  const [data, setData] = useState<Dashboard | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.dashboard().then(setData).catch((e) => setError(String(e)));
    const timer = setInterval(() => {
      api.dashboard().then(setData).catch(() => undefined);
    }, 10_000);
    return () => clearInterval(timer);
  }, []);

  if (error) return <div className="notice error">Cannot reach the SceneWorks API: {error}</div>;
  if (!data) return <div className="empty">Loading…</div>;

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
                    <Link href={`/tasks/${task.id}`}>{task.title}</Link>
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
