"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import { api, errorMessage } from "@/lib/api";
import type { Execution } from "@/lib/types";
import { formatTime, shortId } from "@/lib/format";
import StatusBadge from "@/components/StatusBadge";

const STATUSES = ["QUEUED", "STARTING", "RUNNING", "COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED"];

function ExecutionsContent() {
  const searchParams = useSearchParams();
  const requestedStatus = searchParams.get("status") ?? "";
  const [rows, setRows] = useState<Execution[]>([]);
  const [status, setStatus] = useState(STATUSES.includes(requestedStatus) ? requestedStatus : "");
  const [error, setError] = useState<string | null>(null);
  const [pendingCancel, setPendingCancel] = useState<Set<string>>(new Set());

  useEffect(() => {
    setStatus(STATUSES.includes(requestedStatus) ? requestedStatus : "");
  }, [requestedStatus]);

  const refresh = useCallback(() => {
    const params: Record<string, string> = { limit: "100" };
    if (status) params.status = status;
    api.executions(params)
      .then((next) => {
        setRows(next);
        setError(null);
      })
      .catch((e) => setError(errorMessage(e)));
  }, [status]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const hasActiveRows = rows.some((row) => ["QUEUED", "STARTING", "RUNNING", "CANCELLING"].includes(row.status));
  useEffect(() => {
    if (!hasActiveRows) return;
    const timer = setInterval(() => {
      if (document.visibilityState === "visible") refresh();
    }, 5000);
    return () => clearInterval(timer);
  }, [hasActiveRows, refresh]);

  async function cancel(id: string) {
    setPendingCancel((current) => new Set(current).add(id));
    setRows((current) => current.map((row) => row.id === id ? { ...row, status: "CANCELLING" } : row));
    try {
      await api.cancelExecution(id);
    } catch (e) {
      setRows((current) => current.map((row) => row.id === id ? { ...row, status: "RUNNING" } : row));
      setError(errorMessage(e));
    } finally {
      setPendingCancel((current) => {
        const next = new Set(current);
        next.delete(id);
        return next;
      });
    }
  }

  return (
    <div>
      <div className="row space-between">
        <h1>Executions</h1>
        <select style={{ width: 200 }} value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">All statuses</option>
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </div>
      <p className="muted">Every agent invocation is an execution. Executions survive restarts.</p>

      {error && <div className="notice error">{error}</div>}

      <div className="panel">
        {rows.length === 0 ? (
          <div className="empty">No executions{status ? ` with status ${status}` : ""}.</div>
        ) : (
          <table className="grid">
            <thead>
              <tr>
                <th>Execution</th>
                <th>Role</th>
                <th>Backend</th>
                <th>Status</th>
                <th>Started</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((execution) => (
                <tr key={execution.id}>
                  <td>
                    <Link href={`/executions/${execution.id}`} className="mono">
                      {shortId(execution.id)}
                    </Link>
                    {execution.task_id && (
                      <span className="muted small"> · task #{execution.task_id}</span>
                    )}
                  </td>
                  <td>
                    <span className="badge role">{execution.role}</span>
                  </td>
                  <td className="mono small">{execution.backend}</td>
                  <td>
                    <StatusBadge status={execution.status} />
                  </td>
                  <td className="muted small">{formatTime(execution.started_at)}</td>
                  <td>
                    {["QUEUED", "STARTING", "RUNNING", "CANCELLING"].includes(execution.status) && (
                      <button className="btn small danger" disabled={pendingCancel.has(execution.id)} onClick={() => cancel(execution.id)}>
                        {pendingCancel.has(execution.id) ? "Stopping…" : "Stop"}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

export default function ExecutionsPage() {
  return (
    <Suspense fallback={<h1>Executions</h1>}>
      <ExecutionsContent />
    </Suspense>
  );
}
