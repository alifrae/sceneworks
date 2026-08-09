"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Project, Task } from "@/lib/types";
import StatusBadge from "@/components/StatusBadge";
import { timeAgo } from "@/lib/format";

export default function TasksPage() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [filters, setFilters] = useState({ status: "", project_id: "", role: "" });
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({ project_id: "", title: "", description: "", priority: "medium" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    const params: Record<string, string> = {};
    if (filters.status) params.status = filters.status;
    if (filters.project_id) params.project_id = filters.project_id;
    if (filters.role) params.role = filters.role;
    api.tasks(params).then(setTasks).catch((e) => setError(String(e)));
  }, [filters]);

  useEffect(() => {
    refresh();
    api.projects().then(setProjects).catch(() => undefined);
    const timer = setInterval(refresh, 8000);
    return () => clearInterval(timer);
  }, [refresh]);

  async function create() {
    setBusy(true);
    setError(null);
    try {
      await api.createTask({
        project_id: Number(form.project_id),
        title: form.title,
        description: form.description,
        priority: form.priority,
      });
      setShowForm(false);
      setForm({ project_id: "", title: "", description: "", priority: "medium" });
      refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <div className="row space-between">
        <h1>Tasks</h1>
        <button className="btn primary" onClick={() => setShowForm(!showForm)}>
          {showForm ? "Cancel" : "+ New task"}
        </button>
      </div>

      {error && <div className="notice error">{error}</div>}

      {showForm && (
        <div className="panel">
          <h2>Create task</h2>
          <label className="field">
            Project
            <select
              value={form.project_id}
              onChange={(e) => setForm({ ...form, project_id: e.target.value })}
            >
              <option value="">Select project…</option>
              {projects.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            Title
            <input
              value={form.title}
              onChange={(e) => setForm({ ...form, title: e.target.value })}
              placeholder="Fix incorrect calculation in component X"
            />
          </label>
          <label className="field">
            Description
            <textarea
              rows={3}
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
            />
          </label>
          <label className="field">
            Priority
            <select value={form.priority} onChange={(e) => setForm({ ...form, priority: e.target.value })}>
              <option value="low">low</option>
              <option value="medium">medium</option>
              <option value="high">high</option>
            </select>
          </label>
          <button className="btn primary" onClick={create} disabled={busy || !form.title || !form.project_id}>
            {busy ? "Creating…" : "Create task"}
          </button>
        </div>
      )}

      <div className="panel">
        <div className="row" style={{ marginBottom: 12 }}>
          <select
            style={{ width: 180 }}
            value={filters.status}
            onChange={(e) => setFilters({ ...filters, status: e.target.value })}
          >
            <option value="">All statuses</option>
            {["NEW", "ARCHITECTURE_ANALYSIS", "AWAITING_ARCHITECTURE_APPROVAL", "READY_TO_IMPLEMENT", "IMPLEMENTING", "TESTING", "REVIEWING", "CHANGES_REQUESTED", "READY_FOR_HUMAN", "ACCEPTED", "REJECTED", "FAILED", "CANCELLED"].map((s) => (
              <option key={s} value={s}>
                {s.replace(/_/g, " ")}
              </option>
            ))}
          </select>
          <select
            style={{ width: 180 }}
            value={filters.project_id}
            onChange={(e) => setFilters({ ...filters, project_id: e.target.value })}
          >
            <option value="">All projects</option>
            {projects.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
          <select
            style={{ width: 160 }}
            value={filters.role}
            onChange={(e) => setFilters({ ...filters, role: e.target.value })}
          >
            <option value="">All roles</option>
            {["architect", "engineer", "reviewer"].map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
        </div>

        {tasks.length === 0 ? (
          <div className="empty">No tasks match.</div>
        ) : (
          <table className="grid">
            <thead>
              <tr>
                <th>Task</th>
                <th>Project</th>
                <th>Status</th>
                <th>Current role</th>
                <th>Priority</th>
                <th>Last update</th>
              </tr>
            </thead>
            <tbody>
              {tasks.map((task) => (
                <tr key={task.id}>
                  <td>
                    <Link href={`/tasks/${task.id}`}>{task.title}</Link>
                  </td>
                  <td>{task.project_name}</td>
                  <td>
                    <StatusBadge status={task.status} />
                  </td>
                  <td>
                    {task.current_role ? <span className="badge role">{task.current_role}</span> : "—"}
                  </td>
                  <td className="small">{task.priority}</td>
                  <td className="muted small">{timeAgo(task.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
