"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Project, RepoStatus, Task } from "@/lib/types";
import StatusBadge from "@/components/StatusBadge";
import { timeAgo } from "@/lib/format";

export default function ProjectDetailPage() {
  const { id } = useParams<{ id: string }>();
  const projectId = Number(id);
  const [project, setProject] = useState<Project | null>(null);
  const [status, setStatus] = useState<RepoStatus | null>(null);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [edit, setEdit] = useState(false);
  const [form, setForm] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    api.project(projectId).then(setProject).catch((e) => setError(String(e)));
    api.projectStatus(projectId).then(setStatus).catch(() => undefined);
    api.tasks({ project_id: String(projectId) }).then(setTasks).catch(() => undefined);
  }, [projectId]);

  useEffect(() => refresh(), [refresh]);

  async function save() {
    await api.updateProject(projectId, form);
    setEdit(false);
    refresh();
  }

  if (!project) return <div className="empty">Loading…</div>;

  return (
    <div>
      <p className="small">
        <Link href="/projects">← Projects</Link>
      </p>
      <div className="row space-between">
        <h1>{project.name}</h1>
        <button className="btn small" onClick={() => setEdit(!edit)}>
          {edit ? "Cancel" : "Edit metadata"}
        </button>
      </div>
      <p className="muted">{project.description || "No description."}</p>

      {error && <div className="notice error">{error}</div>}

      {edit && (
        <div className="panel">
          <h2>Edit project</h2>
          <label className="field">
            Description
            <textarea
              rows={2}
              value={form.description ?? project.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
            />
          </label>
          <label className="field">
            Default branch
            <input
              value={form.default_branch ?? project.default_branch}
              onChange={(e) => setForm({ ...form, default_branch: e.target.value })}
            />
          </label>
          <label className="field">
            Test commands (one per line)
            <textarea
              rows={3}
              value={form.test_commands ?? project.test_commands.join("\n")}
              onChange={(e) => setForm({ ...form, test_commands: e.target.value })}
            />
          </label>
          <label className="field">
            Context files (repo-relative paths, one per line)
            <textarea
              rows={3}
              value={form.architecture_context_paths ?? project.architecture_context_paths.join("\n")}
              onChange={(e) => setForm({ ...form, architecture_context_paths: e.target.value })}
              placeholder={"docs/architecture.md\nAGENTS.md"}
            />
          </label>
          <div className="row">
            <button className="btn primary" onClick={save}>
              Save
            </button>
          </div>
        </div>
      )}

      <div className="panel">
        <h2>Repository status</h2>
        {status ? (
          <table className="grid">
            <tbody>
              <tr>
                <td className="muted">Path</td>
                <td className="mono">{project.repository_path}</td>
              </tr>
              <tr>
                <td className="muted">Valid Git repository</td>
                <td>{status.is_git ? "yes" : <span style={{ color: "#e11d48" }}>no — {status.error}</span>}</td>
              </tr>
              <tr>
                <td className="muted">Head branch / commit</td>
                <td className="mono">
                  {status.head_branch || "—"} @ {status.head_commit?.slice(0, 8) || "—"}
                </td>
              </tr>
              <tr>
                <td className="muted">Worktrees</td>
                <td>
                  {status.worktrees.length === 0 ? (
                    <span className="muted">none</span>
                  ) : (
                    status.worktrees.map((w) => (
                      <div key={w.path} className="mono small">
                        {w.branch?.replace("refs/heads/", "") || "detached"} — {w.path}
                      </div>
                    ))
                  )}
                </td>
              </tr>
            </tbody>
          </table>
        ) : (
          <div className="empty">Loading repository status…</div>
        )}
      </div>

      <div className="panel">
        <h2>Tasks</h2>
        {tasks.length === 0 ? (
          <div className="empty">
            No tasks. <Link href="/tasks">Create one</Link>.
          </div>
        ) : (
          <table className="grid">
            <thead>
              <tr>
                <th>Task</th>
                <th>Status</th>
                <th>Current role</th>
                <th>Priority</th>
                <th>Updated</th>
              </tr>
            </thead>
            <tbody>
              {tasks.map((task) => (
                <tr key={task.id}>
                  <td>
                    <Link href={`/tasks/${task.id}`}>{task.title}</Link>
                  </td>
                  <td>
                    <StatusBadge status={task.status} />
                  </td>
                  <td>{task.current_role ? <span className="badge role">{task.current_role}</span> : "—"}</td>
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
