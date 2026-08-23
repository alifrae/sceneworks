"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { api, errorMessage } from "@/lib/api";
import type { Initiative, Project, WorkPackage } from "@/lib/types";
import LoadingShell from "@/components/LoadingShell";

export default function ProjectInitiativesPage() {
  const { id } = useParams<{ id: string }>();
  const projectId = Number(id);
  const [project, setProject] = useState<Project | null>(null);
  const [initiatives, setInitiatives] = useState<Initiative[]>([]);
  const [packages, setPackages] = useState<Record<number, WorkPackage[]>>({});
  const [newInitiative, setNewInitiative] = useState({ title: "", objective: "" });
  const [newPackage, setNewPackage] = useState<Record<number, { key: string; title: string }>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const [projectRow, initiativeRows] = await Promise.all([
      api.project(projectId),
      api.initiatives(projectId),
    ]);
    setProject(projectRow);
    setInitiatives(initiativeRows);
    const packageRows = await Promise.all(
      initiativeRows.map(async (initiative) => [initiative.id, await api.workPackages(initiative.id)] as const),
    );
    setPackages(Object.fromEntries(packageRows));
  }, [projectId]);

  useEffect(() => {
    refresh().catch((e) => setError(errorMessage(e)));
  }, [refresh]);

  async function createInitiative() {
    if (!newInitiative.title.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await api.createInitiative(projectId, {
        title: newInitiative.title.trim(),
        objective: newInitiative.objective.trim(),
      });
      setNewInitiative({ title: "", objective: "" });
      await refresh();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  async function createWorkPackage(initiativeId: number) {
    const draft = newPackage[initiativeId];
    if (!draft?.key.trim() || !draft?.title.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await api.createWorkPackage(initiativeId, {
        key: draft.key.trim(),
        title: draft.title.trim(),
      });
      setNewPackage((current) => ({ ...current, [initiativeId]: { key: "", title: "" } }));
      await refresh();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  if (!project) return <LoadingShell title="Initiatives" />;

  return (
    <div>
      <p className="small">
        <Link href={`/projects/${projectId}`}>← {project.name}</Link>
      </p>
      <div className="row space-between">
        <div>
          <h1>Initiatives</h1>
          <p className="muted">Objectives → work packages → executable SceneWorks tasks.</p>
        </div>
      </div>

      {error && <div className="notice error">{error}</div>}

      <div className="panel">
        <h2>New initiative</h2>
        <label className="field">
          Title
          <input
            value={newInitiative.title}
            onChange={(e) => setNewInitiative({ ...newInitiative, title: e.target.value })}
            placeholder="Multi-file recording support"
            disabled={busy}
          />
        </label>
        <label className="field">
          Objective
          <textarea
            rows={2}
            value={newInitiative.objective}
            onChange={(e) => setNewInitiative({ ...newInitiative, objective: e.target.value })}
            placeholder="What outcome should this initiative deliver?"
            disabled={busy}
          />
        </label>
        <button className="btn primary" onClick={createInitiative} disabled={busy || !newInitiative.title.trim()}>
          Create initiative
        </button>
      </div>

      {initiatives.length === 0 ? (
        <div className="panel empty">No initiatives yet. Tasks can still exist directly under the project.</div>
      ) : (
        initiatives.map((initiative) => {
          const rows = packages[initiative.id] ?? [];
          const draft = newPackage[initiative.id] ?? { key: "", title: "" };
          return (
            <div className="panel" key={initiative.id}>
              <div className="row space-between" style={{ alignItems: "flex-start" }}>
                <div>
                  <h2>{initiative.title}</h2>
                  <p>{initiative.objective || <span className="muted">No objective recorded.</span>}</p>
                </div>
                <span className="status-chip">{initiative.status}</span>
              </div>
              <p className="small muted">
                {initiative.completed_work_packages}/{initiative.work_package_count} work packages completed · {initiative.task_count} tasks
              </p>

              {rows.length === 0 ? (
                <div className="empty">No work packages.</div>
              ) : (
                <table className="grid">
                  <thead>
                    <tr>
                      <th>WP</th>
                      <th>Title</th>
                      <th>Status</th>
                      <th>Depends on</th>
                      <th>Tasks</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((wp) => (
                      <tr key={wp.id}>
                        <td className="mono">{wp.key}</td>
                        <td>{wp.title}</td>
                        <td><span className="status-chip">{wp.status}</span></td>
                        <td className="small">
                          {wp.depends_on.length
                            ? wp.depends_on.map((dep) => rows.find((candidate) => candidate.id === dep)?.key ?? `#${dep}`).join(", ")
                            : "—"}
                        </td>
                        <td>{wp.task_count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}

              <div style={{ marginTop: 16 }}>
                <h3>Add work package</h3>
                <div className="row" style={{ alignItems: "flex-end", flexWrap: "wrap" }}>
                  <label className="field" style={{ minWidth: 120 }}>
                    Key
                    <input
                      value={draft.key}
                      onChange={(e) => setNewPackage((current) => ({ ...current, [initiative.id]: { ...draft, key: e.target.value } }))}
                      placeholder="WP1"
                      disabled={busy}
                    />
                  </label>
                  <label className="field" style={{ minWidth: 260, flex: 1 }}>
                    Title
                    <input
                      value={draft.title}
                      onChange={(e) => setNewPackage((current) => ({ ...current, [initiative.id]: { ...draft, title: e.target.value } }))}
                      placeholder="Session model"
                      disabled={busy}
                    />
                  </label>
                  <button
                    className="btn"
                    onClick={() => createWorkPackage(initiative.id)}
                    disabled={busy || !draft.key.trim() || !draft.title.trim()}
                  >
                    Add
                  </button>
                </div>
                <p className="small muted">Dependencies, acceptance criteria, ordering and status are available through the planning API; the default UI keeps creation lightweight.</p>
              </div>
            </div>
          );
        })
      )}
    </div>
  );
}
