"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, errorMessage } from "@/lib/api";
import type { EngineeringContract, Project } from "@/lib/types";

interface ComposerProps {
  defaultProjectId?: number;
  suppressError?: boolean;
}

type ContractDraft = {
  acceptance: string;
  scope: string;
  forbidden: string;
  tests: string;
};

const EMPTY_CONTRACT: ContractDraft = {
  acceptance: "",
  scope: "",
  forbidden: "",
  tests: "",
};

function lines(value: string): string[] {
  return value
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

function contractFromDraft(draft: ContractDraft): EngineeringContract {
  return {
    required_behavior: [],
    allowed_scope: lines(draft.scope),
    forbidden_changes: lines(draft.forbidden),
    architecture_constraints: [],
    required_tests: lines(draft.tests),
    performance_requirements: [],
    compatibility_requirements: [],
    acceptance_criteria: lines(draft.acceptance),
  };
}

export default function Composer({ defaultProjectId, suppressError = false }: ComposerProps) {
  const router = useRouter();
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState<string>(defaultProjectId ? String(defaultProjectId) : "");
  const [question, setQuestion] = useState("");
  const [showContract, setShowContract] = useState(false);
  const [contract, setContract] = useState<ContractDraft>(EMPTY_CONTRACT);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = () =>
      api
        .projects()
        .then((rows) => {
          if (cancelled) return;
          setProjects(rows);
          setError(null);
          if (!projectId && rows.length > 0) {
            setProjectId(String(defaultProjectId ?? rows[0].id));
          }
        })
        .catch((e) => {
          if (!cancelled) setError(errorMessage(e));
        });

    load();
    const timer = setInterval(() => {
      if (document.visibilityState === "visible") load();
    }, 15_000);
    const onVisible = () => {
      if (document.visibilityState === "visible") load();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      cancelled = true;
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
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
        engineering_contract: contractFromDraft(contract),
      });
      api.taskAction(task.id, "start_architecture").catch(() => undefined);
      router.push(`/work/${task.id}`);
    } catch (e) {
      setError(errorMessage(e));
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
    if (suppressError) {
      return (
        <div className="composer">
          <div className="empty">Project list unavailable — the API is offline.</div>
        </div>
      );
    }
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

      <div style={{ marginTop: 8 }}>
        <button className="link-btn" type="button" onClick={() => setShowContract((value) => !value)} disabled={busy}>
          {showContract ? "Hide engineering contract" : "Add engineering contract"}
        </button>
      </div>

      {showContract && (
        <div className="panel" style={{ marginTop: 8 }}>
          <p className="small muted">
            Optional. One item per line. These constraints become binding for Architect, Engineer and Reviewer once work starts.
          </p>
          <label className="field">
            Acceptance criteria
            <textarea
              rows={3}
              value={contract.acceptance}
              onChange={(e) => setContract({ ...contract, acceptance: e.target.value })}
              placeholder={"Existing behavior remains unchanged\nNew path is covered by tests"}
              disabled={busy}
            />
          </label>
          <label className="field">
            Allowed scope
            <textarea
              rows={2}
              value={contract.scope}
              onChange={(e) => setContract({ ...contract, scope: e.target.value })}
              placeholder={"backend/app/...\nbackend/tests/..."}
              disabled={busy}
            />
          </label>
          <label className="field">
            Forbidden changes
            <textarea
              rows={2}
              value={contract.forbidden}
              onChange={(e) => setContract({ ...contract, forbidden: e.target.value })}
              placeholder="Do not change the public API"
              disabled={busy}
            />
          </label>
          <label className="field">
            Required tests / evidence
            <textarea
              rows={2}
              value={contract.tests}
              onChange={(e) => setContract({ ...contract, tests: e.target.value })}
              placeholder="uv run pytest tests/test_target.py"
              disabled={busy}
            />
          </label>
        </div>
      )}

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
