"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { api, errorMessage } from "@/lib/api";
import { useTasks } from "@/lib/useTasks";
import type { ExecutionMode, Project, Task, WorkItemType } from "@/lib/types";
import WorkRow from "@/components/WorkRow";
import { getWorkView } from "@/lib/workStages";

const FILTERS = [
  { key: "backlog", label: "Backlog" },
  { key: "active", label: "Active" },
  { key: "attention", label: "Needs attention" },
  { key: "done", label: "Done" },
  { key: "all", label: "All" },
] as const;

type FilterKey = (typeof FILTERS)[number]["key"];

function isFilterKey(value: string | null): value is FilterKey {
  return FILTERS.some((f) => f.key === value);
}

function bucket(task: Task): Exclude<FilterKey, "all"> {
  if (task.status === "NEW") return "backlog";
  if (["ACCEPTED", "REJECTED", "CANCELLED"].includes(task.status)) return "done";
  if (getWorkView(task).needsAttention) return "attention";
  return "active";
}

function WorkListContent() {
  const searchParams = useSearchParams();
  const initialFilter = searchParams.get("filter");
  const shared = useTasks();
  const [tasks, setTasks] = useState<Task[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState("");
  const [filter, setFilter] = useState<FilterKey>(isFilterKey(initialFilter) ? initialFilter : "all");
  const [workItemType, setWorkItemType] = useState<WorkItemType | "">("");
  const [mode, setMode] = useState<ExecutionMode | "">("");
  const [priority, setPriority] = useState("");
  const [query, setQuery] = useState("");
  const [error, setError] = useState<string | null>(null);

  const sourceTasks = projectId === "" ? (shared.tasks ?? []) : tasks;
  const listError = projectId === "" ? shared.error : error;

  const refresh = useCallback(() => {
    const params: Record<string, string> = { limit: "200" };
    if (projectId) params.project_id = projectId;
    api.tasks(params).then(setTasks).catch((e) => setError(errorMessage(e)));
  }, [projectId]);

  const isProjectFiltered = projectId !== "";
  useEffect(() => {
    if (isProjectFiltered) refresh();
  }, [isProjectFiltered, refresh]);
  useEffect(() => {
    api.projects().then(setProjects).catch(() => undefined);
  }, []);
  useEffect(() => {
    if (!isProjectFiltered) return;
    const timer = setInterval(() => {
      if (document.visibilityState === "visible") refresh();
    }, 8000);
    return () => clearInterval(timer);
  }, [isProjectFiltered, refresh]);

  const scoped = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return sourceTasks.filter((task) => {
      if (workItemType && task.work_item_type !== workItemType) return false;
      if (priority && task.priority !== priority) return false;
      const effectiveMode = task.resolved_mode ?? task.requested_mode;
      if (mode && effectiveMode !== mode) return false;
      if (needle && !`${task.title}\n${task.description}`.toLowerCase().includes(needle)) return false;
      return true;
    });
  }, [sourceTasks, workItemType, priority, mode, query]);

  const counts = useMemo(() => {
    const next: Record<FilterKey, number> = { backlog: 0, active: 0, attention: 0, done: 0, all: scoped.length };
    for (const task of scoped) next[bucket(task)] += 1;
    return next;
  }, [scoped]);

  const filtered = filter === "all" ? scoped : scoped.filter((task) => bucket(task) === filter);

  return (
    <div>
      <h1>Work</h1>
      <p className="muted">Backlog, active work, decisions and completed results — without project-management overhead.</p>

      {listError && sourceTasks.length === 0 && <div className="notice error">Cannot reach the SceneWorks API: {listError}</div>}

      <div className="filter-bar" style={{ margin: "16px 0 10px" }}>
        {FILTERS.map((f) => (
          <button
            key={f.key}
            className={`btn small ${filter === f.key ? "primary" : ""}`}
            aria-pressed={filter === f.key}
            onClick={() => setFilter(f.key)}
          >
            {f.label} {counts[f.key]}
          </button>
        ))}
      </div>

      <div className="row" style={{ marginBottom: 16, gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search work…"
          aria-label="Search work"
          style={{ minWidth: 220, flex: 1 }}
        />
        <select value={projectId} onChange={(e) => setProjectId(e.target.value)} style={{ width: 180 }} aria-label="Project">
          <option value="">All projects</option>
          {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
        <select value={workItemType} onChange={(e) => setWorkItemType(e.target.value as WorkItemType | "")} aria-label="Type">
          <option value="">All types</option>
          <option value="task">Task</option>
          <option value="bug">Bug</option>
          <option value="feature">Feature</option>
          <option value="idea">Idea</option>
        </select>
        <select value={mode} onChange={(e) => setMode(e.target.value as ExecutionMode | "")} aria-label="Mode">
          <option value="">All modes</option>
          <option value="auto">Auto</option>
          <option value="change">Change</option>
          <option value="investigate">Investigate</option>
          <option value="plan">Plan</option>
          <option value="ask">Ask</option>
        </select>
        <select value={priority} onChange={(e) => setPriority(e.target.value)} aria-label="Priority">
          <option value="">All priorities</option>
          <option value="high">High</option>
          <option value="medium">Medium</option>
          <option value="low">Low</option>
        </select>
      </div>

      <div className="panel">
        {filtered.length === 0 ? (
          listError && sourceTasks.length === 0 ? (
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
