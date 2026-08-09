"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Execution } from "@/lib/types";
import EventLog from "@/components/EventLog";
import Markdown from "@/components/Markdown";
import { formatTime } from "@/lib/format";

export default function ExecutionDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [execution, setExecution] = useState<Execution | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .execution(id)
      .then(setExecution)
      .catch((e) => setError(String(e)));
    const timer = setInterval(() => {
      api.execution(id).then(setExecution).catch(() => undefined);
    }, 4000);
    return () => clearInterval(timer);
  }, [id]);

  if (error) return <div className="notice error">{error}</div>;
  if (!execution) return <div className="empty">Loading…</div>;

  return (
    <div>
      <p className="small">
        <Link href="/executions">← Executions</Link>
        {execution.task_id && (
          <>
            {" "}
            · <Link href={`/tasks/${execution.task_id}`}>task #{execution.task_id}</Link>
          </>
        )}
      </p>
      <h1 className="mono">{execution.id}</h1>
      <p className="muted small">
        Role <span className="badge role">{execution.role}</span> · backend{" "}
        <span className="mono">{execution.backend}</span>
        {execution.model_profile && <> · model profile {execution.model_profile}</>} · status{" "}
        <strong>{execution.status}</strong> · started {formatTime(execution.started_at)} · finished{" "}
        {formatTime(execution.finished_at)}
      </p>

      {execution.error && <div className="notice error">{execution.error}</div>}

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, alignItems: "start" }}>
        <div className="panel">
          <EventLog executionId={execution.id} />
        </div>
        <div>
          <div className="panel">
            <h3>Result</h3>
            {execution.result ? (
              <div className="result-box">
                <Markdown text={execution.result} />
              </div>
            ) : (
              <div className="empty">No result yet.</div>
            )}
          </div>
          {execution.prompt_preview && (
            <div className="panel">
              <h3>Prompt (preview)</h3>
              <pre>{execution.prompt_preview}</pre>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
