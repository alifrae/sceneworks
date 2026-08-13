"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Artifact, Project, Role } from "@/lib/types";
import Markdown from "@/components/Markdown";
import { timeAgo } from "@/lib/format";

export default function CompanyPage() {
  const [roles, setRoles] = useState<Role[]>([]);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [ask, setAsk] = useState({ role: "", project_id: "", question: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [selected, setSelected] = useState<Artifact | null>(null);

  const refresh = useCallback(() => {
    api.companyRoles().then(setRoles).catch((e) => setError(String(e)));
    api.artifacts().then(setArtifacts).catch(() => undefined);
    api.projects().then(setProjects).catch(() => undefined);
  }, []);

  useEffect(() => {
    refresh();
    const timer = setInterval(() => api.artifacts().then(setArtifacts).catch(() => undefined), 5000);
    return () => clearInterval(timer);
  }, [refresh]);

  async function askRole(role: string, question?: string) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const q = question ?? ask.question;
      if (!q.trim()) {
        setError("Write a question first.");
        return;
      }
      setNotice(`Submitting ${role} request…`);
      await api.companyAsk({
        role,
        project_id: ask.project_id ? Number(ask.project_id) : null,
        question: q,
      });
      setNotice(`Request accepted for ${role}. The result will be stored as a company decision.`);
      setAsk({ role: "", project_id: ask.project_id, question: "" });
      setTimeout(() => api.artifacts().then(setArtifacts).catch(() => undefined), 1500);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  const org = (key: string) => roles.find((r) => r.key === key);
  const ceo = org("ceo");
  const cto = org("cto");
  const product = org("product");
  const gtm = org("gtm");
  const architect = org("architect");
  const engineer = org("engineer");
  const reviewer = org("reviewer");

  function NodeCard({ role, isTop }: { role?: Role; isTop?: boolean }) {
    if (!role) return null;
    return (
      <div className="org-node">
        <div>
          <div className="name">{role.display_name}</div>
          <div className="small muted" style={{ maxWidth: 560 }}>
            {role.description}
          </div>
          <div className="small" style={{ marginTop: 6 }}>
            <span className="badge role" style={{ marginRight: 6 }}>
              {role.backend}
            </span>
            {role.model_profile && <span className="badge" style={{ background: "#475569" }}>{role.model_profile}</span>}
            {isTop && <span className="badge" style={{ background: "#92400e", marginLeft: 6 }}>final authority</span>}
          </div>
        </div>
        <div className="actions">
          <button className="btn small" disabled={busy} onClick={() => askRole(role.key)}>
          {busy ? "Starting…" : `Ask ${role.key === "architect" ? "Architect" : role.display_name}`}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div>
      <h1>Team</h1>
      <p className="muted">
        Who works on your requests, and what each role is responsible for. Architect, Engineer
        and Reviewer run automatically as part of every Work Thread — see a specific request's
        thread for their request-specific status. CEO, CTO, Product and GTM can be asked
        directly below; their answers are stored as decisions.
      </p>

      {error && <div className="notice error">{error}</div>}
      {notice && <div className="notice">{notice}</div>}

      <div className="panel">
        <h3>Org chart</h3>
        <div className="role-org">
          <NodeCard role={ceo} isTop />
          <div style={{ textAlign: "center" }} className="muted">│</div>
          {cto && product && gtm && (
            <div className="org-row">
              <NodeCard role={cto} />
              <NodeCard role={product} />
              <NodeCard role={gtm} />
            </div>
          )}
          <div style={{ textAlign: "center" }} className="muted">│</div>
          <NodeCard role={architect} />
          <div style={{ textAlign: "center" }} className="muted">│</div>
          <div className="org-row">
            <NodeCard role={engineer} />
            <NodeCard role={reviewer} />
          </div>
        </div>
      </div>

      <div className="panel">
        <h3>Manual invocation</h3>
        <label className="field">
          Project context (optional)
          <select value={ask.project_id} onChange={(e) => setAsk({ ...ask, project_id: e.target.value })}>
            <option value="">No project context</option>
            {projects.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          Question
          <textarea
            rows={3}
            value={ask.question}
            onChange={(e) => setAsk({ ...ask, question: e.target.value })}
            placeholder="CTO: Evaluate whether Project X should introduce technology Y."
          />
        </label>
        <div className="row">
          <button
            className="btn primary"
            disabled={busy || !ask.question.trim()}
            onClick={() => askRole(ask.role || "cto")}
          >
            {busy ? "Starting…" : "Ask selected role"}
          </button>
          <select value={ask.role} onChange={(e) => setAsk({ ...ask, role: e.target.value })} style={{ width: 200 }}>
            {roles
              .filter((r) => ["ceo", "cto", "product", "gtm", "architect"].includes(r.key))
              .map((r) => (
                <option key={r.key} value={r.key}>
                  {r.display_name}
                </option>
              ))}
          </select>
        </div>
      </div>

      <div className="panel">
        <div className="row space-between">
          <h3>Company decisions / artifacts</h3>
          <button className="btn small" onClick={() => setSelected(null)}>
            Clear selection
          </button>
        </div>
        {artifacts.length === 0 ? (
          <div className="empty">No decisions stored yet.</div>
        ) : (
          <div style={{ display: "grid", gridTemplateColumns: selected ? "1fr 1fr" : "1fr", gap: 24 }}>
            <table className="grid">
              <thead>
                <tr>
                  <th>Role</th>
                  <th>Title</th>
                  <th>Created</th>
                </tr>
              </thead>
              <tbody>
                {artifacts.map((artifact) => (
                  <tr
                    key={artifact.id}
                    style={{ cursor: "pointer" }}
                    onClick={() => setSelected(artifact)}
                  >
                    <td>
                      <span className="badge role">{artifact.role}</span>
                    </td>
                    <td>{artifact.title}</td>
                    <td className="muted small">{timeAgo(artifact.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {selected && (
              <div className="result-box">
                <div className="row space-between">
                  <strong>{selected.title}</strong>
                  <span className="badge role">{selected.role}</span>
                </div>
                <Markdown text={selected.content} />
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
