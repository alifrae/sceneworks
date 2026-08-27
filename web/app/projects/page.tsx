"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
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

const EMPTY_FORM: ProjectForm = {
  name: "",
  description: "",
  repository_path: "",
  test_commands: "",
};
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

function historyEntryToForm(item: RegistrationHistoryEntry): ProjectForm {
  return {
    name: item.name,
    description: item.description,
    repository_path: item.repository_path,
    test_commands: item.test_commands,
  };
}

function isE2ETestProject(project: Project): boolean {
  return (
    project.name.startsWith("e2e-") &&
    project.description === "E2E test project" &&
    project.repository_path.toLowerCase().includes("sceneworks-e2e-")
  );
}

export default function ProjectsPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState<ProjectForm>(EMPTY_FORM);
  const [history, setHistory] = useState<RegistrationHistoryEntry[]>([]);
  const [storageReady, setStorageReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [deleting, setDeleting] = useState<Set<number>>(new Set());
  const [cleaningTests, setCleaningTests] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const e2eProjects = useMemo(() => projects.filter(isE2ETestProject), [projects]);

  const refresh = useCallback(() => {
    api.projects()
      .then((next) => {
        setProjects(next);
        setError(null);
      })
      .catch((e) => setError(errorMessage(e)));
  }, []);

  useEffect(() => refresh(), [refresh]);

  useEffect(() => {
    const storedHistory = readStored<RegistrationHistoryEntry[]>(HISTORY_KEY, []);
    const draft = readStored<ProjectForm | null>(DRAFT_KEY, null);
    setHistory(Array.isArray(storedHistory) ? storedHistory.slice(0, HISTORY_LIMIT) : []);
    if (draft) setForm(draft);
    else if (storedHistory.length > 0) setForm(historyEntryToForm(storedHistory[0]));
    setStorageReady(true);
  }, []);

  useEffect(() => {
    if (!storageReady) return;
    try {
      window.localStorage.setItem(DRAFT_KEY, JSON.stringify(form));
    } catch {
      // Storage is a convenience only; registration must still work without it.
    }
  }, [form, storageReady]);

  function rememberRegistration(value: ProjectForm) {
    const entry: RegistrationHistoryEntry = { ...value, saved_at: new Date().toISOString() };
    const next = [
      entry,
      ...history.filter(
        (item) => item.repository_path.toLowerCase() !== value.repository_path.toLowerCase()
      ),
    ].slice(0, HISTORY_LIMIT);
    setHistory(next);
    try {
      window.localStorage.setItem(HISTORY_KEY, JSON.stringify(next));
      window.localStorage.setItem(DRAFT_KEY, JSON.stringify(value));
    } catch {
      // Non-fatal; browser storage may be disabled.
    }
  }

  function restoreHistory(index: number) {
    const item = history[index];
    if (item) setForm(historyEntryToForm(item));
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
        test_commands: submitted.test_commands
          .split("\n")
          .map((s) => s.trim())
          .filter(Boolean),
      });
      setProjects((current) => [created, ...current]);
      rememberRegistration(submitted);
      // Keep the values as the next draft. Re-registering a familiar checkout
      // should not require retyping every field.
      setShowForm(false);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  async function removeProject(project: Project) {
    const confirmed = window.confirm(
      `Delete “${project.name}” from SceneWorks?\n\n` +
        "This removes its SceneWorks task/history records but never deletes or modifies the Git repository."
    );
    if (!confirmed) return;

    setDeleting((current) => new Set(current).add(project.id));
    setError(null);
    try {
      await api.deleteProject(project.id, true);
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

  async function removeE2ETestData() {
    if (e2eProjects.length === 0) return;
    const confirmed = window.confirm(
      `Remove ${e2eProjects.length} generated E2E test project${e2eProjects.length === 1 ? "" : "s"}?\n\n` +
        "Only projects with the SceneWorks E2E name, description, and temporary-repository signature are matched."
    );
    if (!confirmed) return;

    setCleaningTests(true);
    setError(null);
    const removed = new Set<number>();
    try {
      for (const project of e2eProjects) {
        await api.deleteProject(project.id, true, true);
        removed.add(project.id);
      }
      setProjects((current) => current.filter((project) => !removed.has(project.id)));
    } catch (e) {
      setProjects((current) => current.filter((project) => !removed.has(project.id)));
      setError(`E2E cleanup stopped after removing ${removed.size}: ${errorMessage(e)}`);
    } finally {
      setCleaningTests(false);
    }
  }

  return (
    <div>
      <div className="row space-between">
        <h1>Projects</h1>
        <div className="row">
          {e2eProjects.length > 0 && (
            <button className="btn danger" onClick={removeE2ETestData} disabled={cleaningTests}>
              {cleaningTests ? "Removing E2E data…" : `Remove E2E test data (${e2eProjects.length})`}
            </button>
          )}
          <button className="btn primary" onClick={() => setShowForm(!showForm)}>
            {showForm ? "Cancel" : "+ Add existing repository"}
          </button>
        </div>
      </div>

      {error && <div className="notice error">{error}</div>}

      {showForm && (
        <div className="panel">
          <h2>Register an existing local Git repository</h2>
          {history.length > 0 && (
            <label className="field">
              Recent registrations
              <select defaultValue="" onChange={(e) => restoreHistory(Number(e.target.value))}>
                <option value="" disabled>
                  Restore a previous registration…
                </option>
                {history.map((item, index) => (
                  <option key={`${item.repository_path}-${item.saved_at}`} value={index}>
                    {item.name} — {item.repository_path}
                  </option>
                ))}
              </select>
              <small>Stored only in this browser. Your current draft is also retained across reloads.</small>
            </label>
          )}
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
                <th></th>
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
                  <td style={{ textAlign: "right" }}>
                    <button
                      className="btn small danger"
                      disabled={deleting.has(project.id)}
                      onClick={() => removeProject(project)}
                    >
                      {deleting.has(project.id) ? "Deleting…" : "Delete"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
