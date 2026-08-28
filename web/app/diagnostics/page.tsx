"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { API_URL, api, errorMessage, getRequestDiagnostics, type ApiHealth, type RequestDiagnostic } from "@/lib/api";
import type { Backend } from "@/lib/types";

function latencyClass(value: number | null) {
  if (value === null) return "";
  if (value < 150) return "latency-good";
  if (value < 600) return "latency-warn";
  return "latency-bad";
}

function formatMs(value: number | null) {
  return value === null ? "—" : `${Math.round(value)} ms`;
}

export default function DiagnosticsPage() {
  const [health, setHealth] = useState<ApiHealth | null>(null);
  const [healthLatency, setHealthLatency] = useState<number | null>(null);
  const [backends, setBackends] = useState<Backend[]>([]);
  const [requests, setRequests] = useState<RequestDiagnostic[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const runChecks = useCallback(async () => {
    setBusy(true);
    setError(null);
    const started = performance.now();
    try {
      const nextHealth = await api.health();
      setHealth(nextHealth);
      setHealthLatency(Math.round(performance.now() - started));
      const nextBackends = await api.backends(true);
      setBackends(nextBackends);
    } catch (e) {
      setHealth(null);
      setHealthLatency(Math.round(performance.now() - started));
      setError(errorMessage(e));
    } finally {
      setRequests(getRequestDiagnostics());
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    void runChecks();
    const timer = window.setInterval(() => setRequests(getRequestDiagnostics()), 1000);
    return () => window.clearInterval(timer);
  }, [runChecks]);

  const latestNetwork = useMemo(
    () => requests.filter((item) => item.cause !== "cache-hit").slice(0, 20),
    [requests]
  );

  const slowest = useMemo(() => {
    if (latestNetwork.length === 0) return null;
    return [...latestNetwork].sort((a, b) => b.durationMs - a.durationMs)[0];
  }, [latestNetwork]);

  const browserOrigin = typeof window === "undefined" ? "—" : window.location.origin;
  const frontendMode = process.env.NODE_ENV === "development" ? "development" : "production";

  async function copyDiagnostics() {
    const payload = {
      captured_at: new Date().toISOString(),
      frontend_mode: frontendMode,
      browser_origin: browserOrigin,
      api_url: API_URL,
      health,
      health_latency_ms: healthLatency,
      backends,
      recent_requests: latestNetwork,
    };
    try {
      await navigator.clipboard.writeText(JSON.stringify(payload, null, 2));
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      setError("Could not copy diagnostics to the clipboard.");
    }
  }

  return (
    <div>
      <div className="row space-between">
        <div>
          <h1>Diagnostics</h1>
          <p className="muted">Connectivity, provider health, and browser-to-API timing. This is intentionally smaller than a raw log viewer.</p>
        </div>
        <div className="row">
          <button className="btn" onClick={copyDiagnostics}>{copied ? "Copied" : "Copy diagnostics"}</button>
          <button className="btn primary" onClick={() => void runChecks()} disabled={busy}>{busy ? "Checking…" : "Run checks"}</button>
        </div>
      </div>

      {error && <div className="notice error">{error}</div>}

      <div className="diagnostics-grid">
        <div className="diag-card">
          <div className="diag-label">API</div>
          <div className={`diag-value ${health ? "latency-good" : "latency-bad"}`}>{health ? "Reachable" : "Unavailable"}</div>
          <div className="small muted mono">{API_URL}</div>
        </div>
        <div className="diag-card">
          <div className="diag-label">Health round trip</div>
          <div className={`diag-value ${latencyClass(healthLatency)}`}>{formatMs(healthLatency)}</div>
          <div className="small muted">Browser → FastAPI → browser</div>
        </div>
        <div className="diag-card">
          <div className="diag-label">Frontend mode</div>
          <div className="diag-value">{frontendMode}</div>
          <div className="small muted">{frontendMode === "development" ? "First route visits may compile on demand." : "Routes are prebuilt."}</div>
        </div>
        <div className="diag-card">
          <div className="diag-label">Active agents</div>
          <div className="diag-value">{health?.active_executions ?? "—"}</div>
          <div className="small muted">Reported by the API</div>
        </div>
      </div>

      {frontendMode === "development" && (
        <div className="notice">
          You are running the Next.js development server. The first visit to Work, Team, Projects, or Settings can be dominated by route compilation rather than API latency. Use the Windows launcher in production mode for normal SceneWorks use.
        </div>
      )}

      {slowest && slowest.durationMs >= 600 && (
        <div className="notice">
          Slowest recent API request: <span className="mono">{slowest.path}</span> took {slowest.durationMs} ms; FastAPI reported {formatMs(slowest.serverDurationMs)}. {slowest.serverDurationMs !== null && slowest.durationMs - slowest.serverDurationMs > 400 ? "Most of the delay is outside the API handler." : "The API handler itself accounts for a material part of the delay."}
        </div>
      )}

      <div className="panel">
        <h2>Backend health</h2>
        {backends.length === 0 ? (
          <div className="empty">No backend result yet.</div>
        ) : (
          <table className="grid">
            <thead><tr><th>Backend</th><th>Status</th><th>Version</th><th>Detail</th></tr></thead>
            <tbody>
              {backends.map((backend) => (
                <tr key={backend.key}>
                  <td>{backend.label || backend.key}</td>
                  <td><span className={`badge ${backend.available ? "success" : "error"}`}>{backend.available ? "Available" : "Unavailable"}</span></td>
                  <td className="mono small">{backend.version || "—"}</td>
                  <td className="small muted">{backend.detail || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="panel">
        <div className="row space-between">
          <h2>Recent API requests</h2>
          <span className="small muted">Newest first · in-memory browser diagnostics only</span>
        </div>
        {latestNetwork.length === 0 ? (
          <div className="empty">No API requests captured yet.</div>
        ) : (
          <table className="grid">
            <thead><tr><th>Request</th><th>Status</th><th>Total</th><th>Server</th><th>Cause</th></tr></thead>
            <tbody>
              {latestNetwork.map((item, index) => (
                <tr key={`${item.requestId}-${item.timestamp}-${index}`}>
                  <td><span className="mono small">{item.method} {item.path}</span></td>
                  <td>{item.status || "transport"}</td>
                  <td className={`mono small ${latencyClass(item.durationMs)}`}>{item.durationMs} ms</td>
                  <td className="mono small">{formatMs(item.serverDurationMs)}</td>
                  <td className="small muted">{item.cause}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="panel">
        <h2>Environment</h2>
        <table className="grid"><tbody>
          <tr><td className="muted">Browser origin</td><td className="mono">{browserOrigin}</td></tr>
          <tr><td className="muted">API URL</td><td className="mono">{API_URL}</td></tr>
          <tr><td className="muted">Frontend mode</td><td className="mono">{frontendMode}</td></tr>
        </tbody></table>
        <p className="small muted">Server logs stay in the backend terminal. Keeping raw logs out of the main UI avoids noise and accidental exposure of repository paths or provider diagnostics.</p>
      </div>
    </div>
  );
}
