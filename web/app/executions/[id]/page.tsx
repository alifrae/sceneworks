"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Execution } from "@/lib/types";
import EventLog from "@/components/EventLog";
import Markdown from "@/components/Markdown";
import { formatTime } from "@/lib/format";
import LoadingShell from "@/components/LoadingShell";

export default function ExecutionDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [execution, setExecution] = useState<Execution | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    api.execution(id).then(setExecution).catch((e) => setError(String(e)));
  }, [id]);

  const handleEvent = useCallback((event: { type: string }) => {
    if (event.type.startsWith("execution.")) refresh();
  }, [refresh]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    if (!execution || ["COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED"].includes(execution.status)) return;
    const timer = setInterval(refresh, 10000);
    return () => clearInterval(timer);
  }, [execution?.status, refresh]);

  if (error) return <div className="notice error">{error}</div>;
  if (!execution) return <LoadingShell title="Execution" />;

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

      <div className="split-2">
        <div className="panel">
          <EventLog executionId={execution.id} onEvent={handleEvent} />
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
