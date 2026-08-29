"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api, errorMessage } from "@/lib/api";
import type { Project } from "@/lib/types";
import { timeAgo } from "@/lib/format";

type ProjectForm = {
  name: string;
  description: string;
  repository_path: string;
  test_commands: string;
};

type RegistrationHistoryEntry = ProjectForm & { saved_at: string };

const EMPTY_FORM: ProjectForm = { name: "", description: "", repository_path: "", test_commands: "" };
const DRAFT_KEY = "sceneworks.project-registration-draft.v1";
const HISTORY_KEY = "sceneworks.project-registration-history.v1";
const HISTORY_LIMIT = 10;

function readStored<T>(key: string, fallback: T): T {
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

function toForm(item: RegistrationHistoryEntry): ProjectForm {
  return {
    name: item.name,
    description: item.description,
    repository_path: item.repository_path,
    test_commands: item.test_commands,
  };
}

function looksGenerated(project: Project): boolean {
  const value = `${project.name} ${project.description} ${project.repository_path}`.toLowerCase();
  return value.includes("e2e") || value.includes("sceneworks-e2e") || value.includes("test project");
}

export default function ProjectsPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState<ProjectForm>(EMPTY_FORM);
  const [history, setHistory] = useState<RegistrationHistoryEntry[]>([]);
  const [storageReady, setStorageReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [deleting, setDeleting] = useState<Set<number>>(new Set());
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    api.projects()
      .then((rows) => {
        setProjects(rows);
        setError(null);
      })
      .catch((e) => setError(errorMessage(e)));
  }, []);

  useEffect(() => refresh(), [refresh]);

  useEffect(() => {
    const stored = readStored<RegistrationHistoryEntry[]>(HISTORY_KEY, []);
    const draft = readStored<ProjectForm | null>(DRAFT_KEY, null);
    setHistory(Array.isArray(stored) ? stored.slice(0, HISTORY_LIMIT) : []);
    if (draft) setForm(draft);
    else if (stored[0]) setForm(toForm(stored[0]));
    setStorageReady(true);
  }, []);

  useEffect(() => {
    if (!storageReady) return;
    try {
      window.localStorage.setItem(DRAFT_KEY, JSON.stringify(form));
    } catch {
      // Registration must still work when browser storage is unavailable.
    }
  }, [form, storageReady]);

  function remember(value: ProjectForm) {
    const entry: RegistrationHistoryEntry = { ...value, saved_at: new Date().toISOString() };
    const next = [
      entry,
      ...history.filter((item) => item.repository_path.toLowerCase() !== value.repository_path.toLowerCase()),
    ].slice(0, HISTORY_LIMIT);
    setHistory(next);
    try {
      window.localStorage.setItem(HISTORY_KEY, JSON.stringify(next));
      window.localStorage.setItem(DRAFT_KEY, JSON.stringify(value));
    } catch {
      // Convenience only.
    }
  }

  function restore(index: number) {
    const item = history[index];
    if (!item) return;
    setForm(toForm(item));
    setShowForm(true);
  }

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      const submitted = { ...form };
      const created = await api.createProject({
        name: submitted.name,
        description: submitted.description,
        repository_path: submitted.repository_path,
        test_commands: submitted.test_commands.split("\n").map((value) => value.trim()).filter(Boolean),
      });
      setProjects((current) => [created, ...current]);
      remember(submitted);
      setShowForm(false);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  async function unregister(project: Project) {
    const confirmed = window.confirm(
      `Unregister “${project.name}” from SceneWorks?\n\n` +
        "SceneWorks-owned tasks, evidence and configuration for this registration will be removed. The Git repository and external PCS assets are never deleted. Active EngineeringSessions or PCS processes must be stopped first.",
    );
    if (!confirmed) return;

    setDeleting((current) => new Set(current).add(project.id));
    setError(null);
    try {
      // Force only bypasses stale task/provider-session blockers. The backend
      // still refuses to orphan active EngineeringSessions or managed PCS runs.
      await api.deleteProject(project.id, true, true);
      setProjects((current) => current.filter((item) => item.id !== project.id));
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setDeleting((current) => {
        const next = new Set(current);
        next.delete(project.id);
        return next;
      });
    }
  }

  return (
    <div>
      <div className="page-heading-row">
        <div>
          <h1>Projects</h1>
          <p className="muted">Local repositories registered with SceneWorks. Registration does not move or host the repository.</p>
        </div>
        <button className="btn primary" onClick={() => setShowForm((value) => !value)}>
          {showForm ? "Cancel" : "+ Register repository"}
        </button>
      </div>

      {error && (
        <div className="notice error row space-between">
          <span>{error}</span>
          <button className="btn small" onClick={refresh}>Retry</button>
        </div>
      )}

      {showForm && (
        <div className="panel">
          <h2>Register an existing Git repository</h2>
          {history.length > 0 && (
            <label className="field">
              Previous registration
              <select defaultValue="" onChange={(e) => restore(Number(e.target.value))}>
                <option value="" disabled>Reuse saved values…</option>
                {history.map((item, index) => (
                  <option key={`${item.repository_path}-${item.saved_at}`} value={index}>{item.name} — {item.repository_path}</option>
                ))}
              </select>
              <small>Stored in this browser only.</small>
            </label>
          )}
          <label className="field">
            Name
            <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          </label>
          <label className="field">
            Repository path
            <input value={form.repository_path} onChange={(e) => setForm({ ...form, repository_path: e.target.value })} placeholder="C:/path/to/repo" />
            <small>SceneWorks validates the path and creates isolated worktrees elsewhere.</small>
          </label>
          <label className="field">
            Description
            <textarea rows={2} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
          </label>
          <label className="field">
            Test commands
            <textarea rows={2} value={form.test_commands} onChange={(e) => setForm({ ...form, test_commands: e.target.value })} placeholder="python -m pytest" />
          </label>
          <button className="btn primary" onClick={submit} disabled={busy || !form.name || !form.repository_path}>
            {busy ? "Validating…" : "Register"}
          </button>
        </div>
      )}

      {projects.length === 0 ? (
        <div className="panel"><div className="empty">No projects registered.</div></div>
      ) : (
        <div className="project-card-list">
          {projects.map((project) => (
            <article className="project-card" key={project.id}>
              <div className="project-card-main">
                <div>
                  <Link className="project-card-title" href={`/projects/${project.id}`}>{project.name}</Link>
                  {project.description && <div className="small muted" style={{ marginTop: 3 }}>{project.description}</div>}
                  {looksGenerated(project) && <div className="small" style={{ marginTop: 5 }}><span className="issue-kind bug">generated/test registration</span></div>}
                </div>
                <div>
                  <div className="mono small project-card-path" title={project.repository_path}>{project.repository_path}</div>
                  <div className="small muted" style={{ marginTop: 4 }}>{project.default_branch || "no branch"} · {project.active_task_count} active task{project.active_task_count === 1 ? "" : "s"} · updated {timeAgo(project.updated_at)}</div>
                </div>
                <div className="project-card-actions">
                  <Link className="btn small" href={`/?project=${project.id}`}>New work</Link>
                  <Link className="btn small" href={`/projects/${project.id}`}>Open</Link>
                </div>
              </div>
              <div className="project-card-footer">
                <span className="small muted">SceneWorks registration #{project.id}</span>
                <button className="danger-link" disabled={deleting.has(project.id)} onClick={() => unregister(project)}>
                  {deleting.has(project.id) ? "Unregistering…" : "Unregister from SceneWorks"}
                </button>
              </div>
            </article>
          ))}
        </div>
      )}
    </div>
  );
}
