"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import { api, errorMessage } from "@/lib/api";
import { useTasks } from "@/lib/useTasks";
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
  // Unfiltered view reads the app-wide tasks snapshot (shared with the
  // sidebar and the homepage) instead of issuing a second parallel list
  // request. A project filter is a genuinely different query and keeps its
  // own refresh loop.
  const shared = useTasks();
  const [tasks, setTasks] = useState<Task[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState("");
  const [filter, setFilter] = useState<FilterKey>(isFilterKey(initialFilter) ? initialFilter : "all");
  const [error, setError] = useState<string | null>(null);

  const filteredTasks = projectId === "" ? (shared.tasks ?? []) : tasks;
  const listError = projectId === "" ? shared.error : error;

  const refresh = useCallback(() => {
    const params: Record<string, string> = { limit: "200" };
    if (projectId) params.project_id = projectId;
    api.tasks(params).then(setTasks).catch((e) => setError(errorMessage(e)));
  }, [projectId]);

  const isFiltered = projectId !== "";
  useEffect(() => {
    if (isFiltered) refresh();
  }, [isFiltered, refresh]);
  useEffect(() => {
    api.projects().then(setProjects).catch(() => undefined);
  }, []);
  useEffect(() => {
    if (!isFiltered) return;
    const timer = setInterval(() => {
      if (document.visibilityState === "visible") refresh();
    }, 8000);
    return () => clearInterval(timer);
  }, [isFiltered, refresh]);

  const filtered = filteredTasks.filter((t) => {
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

      {listError && filteredTasks.length === 0 && <div className="notice error">Cannot reach the SceneWorks API: {listError}</div>}

      <div className="row space-between" style={{ margin: "16px 0" }}>
        <div className="filter-bar">
          {FILTERS.map((f) => (
            <button
              key={f.key}
              className={`btn small ${filter === f.key ? "primary" : ""}`}
              aria-pressed={filter === f.key}
              onClick={() => setFilter(f.key)}
            >
              {f.label}
            </button>
          ))}
        </div>
        <select value={projectId} onChange={(e) => setProjectId(e.target.value)} style={{ width: 200 }}>
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
          listError && filteredTasks.length === 0 ? (
            <div className="empty">Unavailable — API offline.</div>
          ) : (
            <div className="empty">Nothing here.</div>
          )
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
