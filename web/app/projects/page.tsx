"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Project } from "@/lib/types";
import { timeAgo } from "@/lib/format";

export default function ProjectsPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({
    name: "",
    description: "",
    repository_path: "",
    test_commands: "",
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    api.projects().then(setProjects).catch((e) => setError(String(e)));
  }, []);

  useEffect(() => refresh(), [refresh]);

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      await api.createProject({
        name: form.name,
        description: form.description,
        repository_path: form.repository_path,
        test_commands: form.test_commands
          .split("\n")
          .map((s) => s.trim())
          .filter(Boolean),
      });
      setShowForm(false);
      setForm({ name: "", description: "", repository_path: "", test_commands: "" });
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
        <h1>Projects</h1>
        <button className="btn primary" onClick={() => setShowForm(!showForm)}>
          {showForm ? "Cancel" : "+ Add existing repository"}
        </button>
      </div>

      {error && <div className="notice error">{error}</div>}

      {showForm && (
        <div className="panel">
          <h2>Register an existing local Git repository</h2>
          <label className="field">
            Name
            <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          </label>
          <label className="field">
            Repository path (absolute)
            <input
              value={form.repository_path}
              onChange={(e) => setForm({ ...form, repository_path: e.target.value })}
              placeholder="C:/path/to/my/repo"
            />
            <small>The repository stays untouched; SceneWorks creates isolated worktrees outside it.</small>
          </label>
          <label className="field">
            Description
            <textarea
              rows={2}
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
            />
          </label>
          <label className="field">
            Test commands (one per line)
            <textarea
              rows={2}
              value={form.test_commands}
              onChange={(e) => setForm({ ...form, test_commands: e.target.value })}
              placeholder={"python -m pytest"}
            />
          </label>
          <div className="row">
            <button className="btn primary" onClick={submit} disabled={busy || !form.name || !form.repository_path}>
              {busy ? "Validating…" : "Register project"}
            </button>
          </div>
        </div>
      )}

      <div className="panel">
        {projects.length === 0 ? (
          <div className="empty">No projects yet. Register a local Git repository to begin.</div>
        ) : (
          <table className="grid">
            <thead>
              <tr>
                <th>Name</th>
                <th>Repository</th>
                <th>Branch</th>
                <th>Active tasks</th>
                <th>Updated</th>
              </tr>
            </thead>
            <tbody>
              {projects.map((project) => (
                <tr key={project.id}>
                  <td>
                    <Link href={`/projects/${project.id}`}>{project.name}</Link>
                    {project.description && (
                      <div className="muted small">{project.description}</div>
                    )}
                  </td>
                  <td className="mono small">{project.repository_path}</td>
                  <td className="mono small">{project.default_branch || "—"}</td>
                  <td>{project.active_task_count}</td>
                  <td className="muted small">{timeAgo(project.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
