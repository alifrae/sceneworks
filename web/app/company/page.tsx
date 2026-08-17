"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, errorMessage } from "@/lib/api";
import type { Artifact, Project, Role } from "@/lib/types";
import Markdown from "@/components/Markdown";
import { timeAgo } from "@/lib/format";

export default function CompanyPage() {
  // `null` means "not resolved yet" — the org chart must not render from an
  // empty default, which produced a broken diagram of bare connector pipes
  // whenever the roles request failed or lagged.
  const [roles, setRoles] = useState<Role[] | null>(null);
  const [rolesError, setRolesError] = useState<string | null>(null);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [artifactsError, setArtifactsError] = useState<string | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectsError, setProjectsError] = useState<string | null>(null);
  const [ask, setAsk] = useState({ role: "", project_id: "", question: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [selected, setSelected] = useState<Artifact | null>(null);

  const loadRoles = useCallback(() => {
    api
      .companyRoles()
      .then((rows) => {
        setRoles(rows);
        setRolesError(null);
      })
      .catch((e) => setRolesError(errorMessage(e)));
  }, []);

  const loadArtifacts = useCallback(() => {
    api
      .artifacts()
      .then((rows) => {
        setArtifacts(rows);
        setArtifactsError(null);
      })
      .catch((e) => setArtifactsError(errorMessage(e)));
  }, []);

  const loadProjects = useCallback(() => {
    api
      .projects()
      .then((rows) => {
        setProjects(rows);
        setProjectsError(null);
      })
      .catch((e) => setProjectsError(errorMessage(e)));
  }, []);

  const retryAll = useCallback(() => {
    loadRoles();
    loadArtifacts();
    loadProjects();
  }, [loadRoles, loadArtifacts, loadProjects]);

  const rolesRef = useRef<Role[] | null>(null);

  useEffect(() => {
    loadRoles();
    loadArtifacts();
    loadProjects();
    const artifactsTimer = setInterval(() => {
      if (document.visibilityState === "visible") loadArtifacts();
    }, 5000);
    // Roles and projects change rarely but the page must recover from a
    // backend restart instead of staying in its failed state forever.
    const staticTimer = setInterval(() => {
      if (document.visibilityState === "visible") {
        if (rolesRef.current === null) loadRoles();
        loadProjects();
      }
    }, 30_000);
    const onVisible = () => {
      if (document.visibilityState === "visible") {
        loadArtifacts();
        if (rolesRef.current === null) loadRoles();
      }
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      clearInterval(artifactsTimer);
      clearInterval(staticTimer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [loadRoles, loadArtifacts, loadProjects]);

  useEffect(() => {
    rolesRef.current = roles;
  }, [roles]);

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
      setTimeout(() => loadArtifacts(), 1500);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  const org = (key: string) => roles?.find((r) => r.key === key);
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

  const orgBody = (() => {
    if (roles === null && rolesError === null) {
      return <div className="empty">Loading team…</div>;
    }
    if (!roles || roles.length === 0) {
      return (
        <div className="empty">
          {rolesError ? "Team unavailable — the roles API did not respond." : "No roles configured."}
          <div style={{ marginTop: 8 }}>
            <button className="btn small" onClick={retryAll}>Retry</button>
          </div>
        </div>
      );
    }
    return (
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
    );
  })();

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
        {orgBody}
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
          {projectsError && <small className="muted">Project list unavailable — {projectsError}</small>}
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
            {(roles ?? [])
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
        {artifactsError ? (
          <div className="empty">
            Decisions unavailable — the API did not respond.
            <div style={{ marginTop: 8 }}>
              <button className="btn small" onClick={retryAll}>Retry</button>
            </div>
          </div>
        ) : artifacts.length === 0 ? (
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
