"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { api, errorMessage } from "@/lib/api";
import {
  deleteNewTask,
  deleteTaskAttachment,
  uploadTaskAttachment,
} from "@/lib/attachments";
import type { EngineeringContract, Project, TaskAttachment } from "@/lib/types";

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

const ATTACHMENT_ACCEPT = ".png,.jpg,.jpeg,.webp,.pdf,.txt,.md,.json,.csv";
const MAX_ATTACHMENT_COUNT = 8;
const MAX_ATTACHMENT_BYTES = 20_000_000;
const MAX_TASK_ATTACHMENT_BYTES = 50_000_000;

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

function sizeLabel(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function Composer({ defaultProjectId, suppressError = false }: ComposerProps) {
  const router = useRouter();
  const fileInput = useRef<HTMLInputElement | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState<string>(defaultProjectId ? String(defaultProjectId) : "");
  const [question, setQuestion] = useState("");
  const [attachments, setAttachments] = useState<File[]>([]);
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

  function chooseFiles(files: FileList | null) {
    if (!files?.length) return;
    const next = [...attachments, ...Array.from(files)];
    if (next.length > MAX_ATTACHMENT_COUNT) {
      setError(`A task can contain at most ${MAX_ATTACHMENT_COUNT} attachments.`);
      return;
    }
    const tooLarge = next.find((file) => file.size > MAX_ATTACHMENT_BYTES);
    if (tooLarge) {
      setError(`${tooLarge.name} exceeds the 20 MB per-file limit.`);
      return;
    }
    const total = next.reduce((sum, file) => sum + file.size, 0);
    if (total > MAX_TASK_ATTACHMENT_BYTES) {
      setError("Attachments exceed the 50 MB total task limit.");
      return;
    }
    setError(null);
    setAttachments(next);
    if (fileInput.current) fileInput.current.value = "";
  }

  async function send() {
    if (!question.trim() || !projectId) return;
    setBusy(true);
    setError(null);
    let taskId: number | null = null;
    const uploaded: TaskAttachment[] = [];
    try {
      const title = question.trim().slice(0, 120);
      const task = await api.createTask({
        project_id: Number(projectId),
        title,
        description: question.trim(),
        priority: "medium",
        engineering_contract: contractFromDraft(contract),
      });
      taskId = task.id;
      try {
        for (const file of attachments) {
          uploaded.push(await uploadTaskAttachment(task.id, file));
        }
      } catch (attachmentError) {
        // Attachment binding is atomic from the user's perspective: never start
        // a task with only a subset of the context they selected.
        for (const item of uploaded) {
          await deleteTaskAttachment(task.id, item.id).catch(() => undefined);
        }
        await deleteNewTask(task.id).catch(() => undefined);
        taskId = null;
        throw attachmentError;
      }
      await api.taskAction(task.id, "start_architecture");
      router.push(`/work/${task.id}`);
    } catch (e) {
      setError(
        taskId
          ? `Task ${taskId} was created but could not be started: ${errorMessage(e)}`
          : errorMessage(e),
      );
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

      {attachments.length > 0 && (
        <div className="attachment-chips" aria-label="Task attachments">
          {attachments.map((file, index) => (
            <span className="attachment-chip" key={`${file.name}-${file.size}-${index}`}>
              <span>{file.name}</span>
              <span className="muted small">{sizeLabel(file.size)}</span>
              <button
                type="button"
                className="link-btn"
                aria-label={`Remove ${file.name}`}
                disabled={busy}
                onClick={() => setAttachments((current) => current.filter((_, i) => i !== index))}
              >
                ×
              </button>
            </span>
          ))}
        </div>
      )}

      <div className="composer-tools">
        <input
          ref={fileInput}
          type="file"
          multiple
          accept={ATTACHMENT_ACCEPT}
          hidden
          onChange={(e) => chooseFiles(e.target.files)}
          disabled={busy}
        />
        <button
          className="link-btn"
          type="button"
          onClick={() => fileInput.current?.click()}
          disabled={busy || attachments.length >= MAX_ATTACHMENT_COUNT}
        >
          Attach screenshot, PDF or context file
        </button>
        <span className="muted small">PNG/JPEG/WebP, PDF, TXT/MD/JSON/CSV · 20 MB/file</span>
      </div>

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
          {busy ? (attachments.length ? "Uploading context…" : "Sending…") : "Send"}
        </button>
      </div>
      {error && <div className="notice error">{error}</div>}
    </div>
  );
}
