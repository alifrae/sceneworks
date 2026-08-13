"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Project } from "@/lib/types";

interface ComposerProps {
  defaultProjectId?: number;
}

// The primary entry point: "ask the team" creates a Task and immediately
// starts the workflow (start_architecture), then navigates to its Work
// Thread. Both calls are the same acknowledgement-oriented mutations
// WP-WEB-1 already made fast; the second call is fired without blocking
// navigation so the click-to-thread transition stays local-first.
export default function Composer({ defaultProjectId }: ComposerProps) {
  const router = useRouter();
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState<string>(defaultProjectId ? String(defaultProjectId) : "");
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .projects()
      .then((rows) => {
        setProjects(rows);
        if (!projectId && rows.length > 0) {
          setProjectId(String(defaultProjectId ?? rows[0].id));
        }
      })
      .catch((e) => setError(String(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function send() {
    if (!question.trim() || !projectId) return;
    setBusy(true);
    setError(null);
    try {
      const title = question.trim().slice(0, 120);
      const task = await api.createTask({
        project_id: Number(projectId),
        title,
        description: question.trim(),
        priority: "medium",
      });
      api.taskAction(task.id, "start_architecture").catch(() => undefined);
      router.push(`/work/${task.id}`);
    } catch (e) {
      setError(String(e));
      setBusy(false);
    }
  }

  if (projects.length === 0 && !error) {
    return (
      <div className="composer">
        <div className="empty">Loading projects…</div>
      </div>
    );
  }

  if (projects.length === 0 && error) {
    return <div className="notice error">Cannot reach the SceneWorks API: {error}</div>;
  }

  return (
    <div className="composer">
      <textarea
        className="composer-input"
        rows={3}
        placeholder="Ask the team… e.g. Find why startup went from 8s to 30s and fix it."
        value={question}
        onChange={(e) => setQuestion(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
            e.preventDefault();
            send();
          }
        }}
        disabled={busy}
      />
      <div className="row space-between composer-footer">
        {projects.length > 1 ? (
          <label className="composer-project">
            Project
            <select value={projectId} onChange={(e) => setProjectId(e.target.value)} disabled={busy}>
              {projects.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </label>
        ) : (
          <span className="muted small">Project: {projects[0]?.name}</span>
        )}
        <button className="btn primary" onClick={send} disabled={busy || !question.trim() || !projectId}>
          {busy ? "Sending…" : "Send"}
        </button>
      </div>
      {error && <div className="notice error">{error}</div>}
    </div>
  );
}
