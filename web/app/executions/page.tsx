"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Execution } from "@/lib/types";
import { formatTime, shortId } from "@/lib/format";

const STATUS_COLOR: Record<string, string> = {
  QUEUED: "#94a3b8",
  STARTING: "#3b82f6",
  RUNNING: "#3b82f6",
  COMPLETED: "#22c55e",
  FAILED: "#ef4444",
  CANCELLED: "#94a3b8",
  INTERRUPTED: "#ef4444",
};

export default function ExecutionsPage() {
  const [rows, setRows] = useState<Execution[]>([]);
  const [status, setStatus] = useState("");
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    const params: Record<string, string> = { limit: "200" };
    if (status) params.status = status;
    api.executions(params).then(setRows).catch((e) => setError(String(e)));
  }, [status]);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 5000);
    return () => clearInterval(timer);
  }, [refresh]);

  async function cancel(id: string) {
    try {
      await api.cancelExecution(id);
      refresh();
    } catch (e) {
      setError(String(e));
    }
  }

  return (
    <div>
      <div className="row space-between">
        <h1>Executions</h1>
        <select style={{ width: 200 }} value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">All statuses</option>
          {["QUEUED", "STARTING", "RUNNING", "COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED"].map((s) => (
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
          <div className="empty">No executions yet.</div>
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
                    <span className="badge" style={{ background: STATUS_COLOR[execution.status] ?? "#64748b" }}>
                      {execution.status}
                    </span>
                  </td>
                  <td className="muted small">{formatTime(execution.started_at)}</td>
                  <td>
                    {["QUEUED", "STARTING", "RUNNING"].includes(execution.status) && (
                      <button className="btn small danger" onClick={() => cancel(execution.id)}>
                        Stop
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
