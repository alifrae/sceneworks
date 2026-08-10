"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { Project, ProjectMemory } from "@/lib/types";
import { timeAgo } from "@/lib/format";

const TYPES = [
  { value: "initiative_summary", label: "Initiative Summary" },
  { value: "architecture_decision", label: "Architecture Decision" },
  { value: "product_decision", label: "Product Decision" },
  { value: "technology_decision", label: "Technology Decision" },
  { value: "constraint", label: "Constraint" },
];

const STATUSES = [
  { value: "proposed", label: "Proposed" },
  { value: "accepted", label: "Accepted" },
  { value: "archived", label: "Archived" },
  { value: "superseded", label: "Superseded" },
];

function typeLabel(t: string) {
  return TYPES.find((x) => x.value === t)?.label ?? t;
}

function statusLabel(s: string) {
  return STATUSES.find((x) => x.value === s)?.label ?? s;
}

export default function ProjectMemoryPage() {
  const { id } = useParams<{ id: string }>();
  const projectId = Number(id);
  const [project, setProject] = useState<Project | null>(null);
  const [memories, setMemories] = useState<ProjectMemory[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [editId, setEditId] = useState<number | null>(null);
  const [filterType, setFilterType] = useState("");
  const [filterStatus, setFilterStatus] = useState("");
  const [searchQuery, setSearchQuery] = useState("");

  const [form, setForm] = useState({
    type: "architecture_decision",
    title: "",
    content: "",
    status: "proposed",
    tags: "",
    source: "",
    source_task_id: "",
  });

  const refresh = useCallback(() => {
    api.project(projectId).then(setProject).catch((e) => setError(String(e)));
    const params: Record<string, string> = {};
    if (filterType) params.types = filterType;
    if (filterStatus) params.status = filterStatus;
    if (searchQuery) params.query = searchQuery;
    api.memoryList(projectId, params).then(setMemories).catch(() => undefined);
  }, [projectId, filterType, filterStatus, searchQuery]);

  useEffect(() => refresh(), [refresh]);

  function resetForm() {
    setForm({
      type: "architecture_decision",
      title: "",
      content: "",
      status: "proposed",
      tags: "",
      source: "",
      source_task_id: "",
    });
    setEditId(null);
    setShowForm(false);
  }

  async function save() {
    const body: Record<string, unknown> = {
      type: form.type,
      title: form.title,
      content: form.content,
      status: form.status,
      tags: form.tags ? form.tags.split(",").map((t) => t.trim()) : [],
      source: form.source || undefined,
      source_task_id: form.source_task_id ? Number(form.source_task_id) : undefined,
    };
    try {
      if (editId) {
        await api.memoryUpdate(projectId, editId, body);
      } else {
        await api.memoryCreate(projectId, body);
      }
      resetForm();
      refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  }

  async function archive(memoryId: number) {
    await api.memoryArchive(projectId, memoryId);
    refresh();
  }

  async function editMemory(mem: ProjectMemory) {
    setForm({
      type: mem.type,
      title: mem.title,
      content: mem.content,
      status: mem.status,
      tags: (mem.tags ?? []).join(", "),
      source: mem.source ?? "",
      source_task_id: mem.source_task_id ? String(mem.source_task_id) : "",
    });
    setEditId(mem.id);
    setShowForm(true);
  }

  if (!project) return <div className="empty">Loading…</div>;

  return (
    <div>
      <p className="small">
        <Link href={`/projects/${projectId}`}>← {project.name}</Link>
      </p>
      <div className="row space-between">
        <h1>Project Memory</h1>
        <button className="btn" onClick={() => { resetForm(); setShowForm(true); }}>
          + New
        </button>
      </div>

      {error && <div className="notice error">{error}</div>}

      <div className="row" style={{ gap: 8, marginBottom: 16 }}>
        <input
          placeholder="Search…"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          style={{ flex: 1 }}
        />
        <select value={filterType} onChange={(e) => setFilterType(e.target.value)}>
          <option value="">All types</option>
          {TYPES.map((t) => (
            <option key={t.value} value={t.value}>{t.label}</option>
          ))}
        </select>
        <select value={filterStatus} onChange={(e) => setFilterStatus(e.target.value)}>
          <option value="">All statuses</option>
          {STATUSES.map((s) => (
            <option key={s.value} value={s.value}>{s.label}</option>
          ))}
        </select>
      </div>

      {showForm && (
        <div className="panel" style={{ marginBottom: 16 }}>
          <h3>{editId ? "Edit memory" : "New memory"}</h3>
          <div className="row" style={{ gap: 8 }}>
            <select
              value={form.type}
              onChange={(e) => setForm({ ...form, type: e.target.value })}
            >
              {TYPES.map((t) => (
                <option key={t.value} value={t.value}>{t.label}</option>
              ))}
            </select>
            <select
              value={form.status}
              onChange={(e) => setForm({ ...form, status: e.target.value })}
            >
              {STATUSES.map((s) => (
                <option key={s.value} value={s.value}>{s.label}</option>
              ))}
            </select>
          </div>
          <input
            placeholder="Title"
            value={form.title}
            onChange={(e) => setForm({ ...form, title: e.target.value })}
            style={{ marginTop: 8 }}
          />
          <textarea
            rows={4}
            placeholder="Content"
            value={form.content}
            onChange={(e) => setForm({ ...form, content: e.target.value })}
            style={{ marginTop: 8, width: "100%" }}
          />
          <div className="row" style={{ gap: 8, marginTop: 8 }}>
            <input
              placeholder="Tags (comma-separated)"
              value={form.tags}
              onChange={(e) => setForm({ ...form, tags: e.target.value })}
              style={{ flex: 1 }}
            />
            <input
              placeholder="Source (e.g. manual, architect)"
              value={form.source}
              onChange={(e) => setForm({ ...form, source: e.target.value })}
              style={{ flex: 1 }}
            />
          </div>
          <div className="row" style={{ gap: 8, marginTop: 8 }}>
            <button className="btn" onClick={save}>{editId ? "Update" : "Create"}</button>
            <button className="btn small" onClick={resetForm}>Cancel</button>
          </div>
        </div>
      )}

      {memories.length === 0 && (
        <div className="empty">No memory items. Create one above to get started.</div>
      )}

      {memories.map((mem) => (
        <div key={mem.id} className="panel" style={{ marginBottom: 8 }}>
          <div className="row space-between">
            <strong>
              <span className="muted">[{typeLabel(mem.type)}]</span> {mem.title}
            </strong>
            <span className="muted">{statusLabel(mem.status)}</span>
          </div>
          <pre style={{ whiteSpace: "pre-wrap", margin: "8px 0" }}>
            {mem.content.length > 500
              ? mem.content.slice(0, 500) + "…"
              : mem.content}
          </pre>
          <div className="row space-between" style={{ marginTop: 4 }}>
            <span className="small muted">
              {mem.tags?.length ? `Tags: ${mem.tags.join(", ")} · ` : ""}
              Source: {mem.source ?? "unknown"}
              {mem.source_task_id ? ` · Task #${mem.source_task_id}` : ""}
              {" · "}{timeAgo(mem.updated_at)}
              {mem.supersedes_id ? ` · Supersedes #${mem.supersedes_id}` : ""}
            </span>
            <div className="row" style={{ gap: 4 }}>
              <button className="btn small" onClick={() => editMemory(mem)}>Edit</button>
              {mem.status === "accepted" && (
                <button className="btn small" onClick={() => archive(mem.id)}>Archive</button>
              )}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
