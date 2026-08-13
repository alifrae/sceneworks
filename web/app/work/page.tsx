"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Project, Task } from "@/lib/types";
import WorkRow from "@/components/WorkRow";
import { getWorkView } from "@/lib/workStages";

const FILTERS = [
  { key: "attention", label: "Needs attention" },
  { key: "active", label: "Active" },
  { key: "completed", label: "Completed" },
  { key: "all", label: "All" },
] as const;

type FilterKey = (typeof FILTERS)[number]["key"];

function isFilterKey(value: string | null): value is FilterKey {
  return FILTERS.some((f) => f.key === value);
}

function WorkListContent() {
  const searchParams = useSearchParams();
  const initialFilter = searchParams.get("filter");
  const [tasks, setTasks] = useState<Task[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState("");
  const [filter, setFilter] = useState<FilterKey>(isFilterKey(initialFilter) ? initialFilter : "all");
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    const params: Record<string, string> = { limit: "200" };
    if (projectId) params.project_id = projectId;
    api.tasks(params).then(setTasks).catch((e) => setError(String(e)));
  }, [projectId]);

  useEffect(() => refresh(), [refresh]);
  useEffect(() => {
    api.projects().then(setProjects).catch(() => undefined);
  }, []);
  useEffect(() => {
    const timer = setInterval(refresh, 8000);
    return () => clearInterval(timer);
  }, [refresh]);

  const filtered = tasks.filter((t) => {
    const view = getWorkView(t);
    if (filter === "attention") return view.needsAttention;
    if (filter === "active") return !view.needsAttention && view.exceptional === "none" && view.stage !== "completed";
    if (filter === "completed") return view.stage === "completed" || view.exceptional === "cancelled";
    return true;
  });

  return (
    <div>
      <h1>Work</h1>
      <p className="muted">Everything the team is doing, has done, or needs you for.</p>

      {error && <div className="notice error">{error}</div>}

      <div className="row" style={{ margin: "12px 0" }}>
        {FILTERS.map((f) => (
          <button
            key={f.key}
            className={`btn small ${filter === f.key ? "primary" : ""}`}
            onClick={() => setFilter(f.key)}
          >
            {f.label}
          </button>
        ))}
        <select value={projectId} onChange={(e) => setProjectId(e.target.value)} style={{ width: 200, marginLeft: "auto" }}>
          <option value="">All projects</option>
          {projects.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
      </div>

      <div className="panel">
        {filtered.length === 0 ? (
          <div className="empty">Nothing here.</div>
        ) : (
          <div className="work-list">
            {filtered.map((task) => (
              <WorkRow key={task.id} task={task} reason={getWorkView(task).attentionReason} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export default function WorkListPage() {
  return (
    <Suspense fallback={<h1>Work</h1>}>
      <WorkListContent />
    </Suspense>
  );
}
