"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { API_URL, api, errorMessage, getRequestDiagnostics, type ApiHealth, type RequestDiagnostic } from "@/lib/api";
import type { Backend } from "@/lib/types";

type ComponentName = "api" | "web" | "mcp_tunnel";
type SystemComponent = {
  state: string;
  consecutive_failures: number;
  restart_attempts: number;
  last_transition_at: number | null;
  healthy_since: number | null;
  enabled: boolean;
};
type SystemStatus = {
  aggregate_state: string;
  components: Partial<Record<ComponentName, SystemComponent>>;
};

const COMPONENT_LABELS: Record<ComponentName, string> = {
  api: "API",
  web: "Web",
  mcp_tunnel: "MCP tunnel",
};

function latencyClass(value: number | null) {
  if (value === null) return "";
  if (value < 150) return "latency-good";
  if (value < 600) return "latency-warn";
  return "latency-bad";
}

function formatMs(value: number | null) {
  return value === null ? "—" : `${Math.round(value)} ms`;
}

function lifecycleBadge(state: string) {
  if (state === "HEALTHY") return "success";
  if (["UNHEALTHY", "DEGRADED", "UNKNOWN"].includes(state)) return "error";
  return "";
}

function sleep(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

export default function DiagnosticsPage() {
  const [health, setHealth] = useState<ApiHealth | null>(null);
  const [healthLatency, setHealthLatency] = useState<number | null>(null);
  const [backends, setBackends] = useState<Backend[]>([]);
  const [requests, setRequests] = useState<RequestDiagnostic[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [systemStatus, setSystemStatus] = useState<SystemStatus | null>(null);
  const [systemError, setSystemError] = useState<string | null>(null);
  const [systemBusy, setSystemBusy] = useState<ComponentName | "all" | null>(null);

  const loadSystemStatus = useCallback(async () => {
    try {
      const response = await fetch("/api/supervisor/status", { cache: "no-store" });
      if (!response.ok) throw new Error("Lifecycle supervisor is unavailable.");
      setSystemStatus(await response.json());
      setSystemError(null);
    } catch (e) {
      setSystemStatus(null);
      setSystemError(e instanceof Error ? e.message : "Lifecycle supervisor is unavailable.");
    }
  }, []);

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
      void loadSystemStatus();
    }
  }, [loadSystemStatus]);

  useEffect(() => {
    void runChecks();
    const requestTimer = window.setInterval(() => setRequests(getRequestDiagnostics()), 1000);
    const systemTimer = window.setInterval(() => void loadSystemStatus(), 5000);
    return () => {
      window.clearInterval(requestTimer);
      window.clearInterval(systemTimer);
    };
  }, [loadSystemStatus, runChecks]);

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

  async function restartService(component: ComponentName | "all") {
    const label = component === "all" ? "SceneWorks" : COMPONENT_LABELS[component];
    if (!window.confirm(`Restart ${label}? Active work using that service may be interrupted.`)) return;
    setSystemBusy(component);
    setSystemError(null);
    try {
      const response = await fetch("/api/supervisor/restart", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ component }),
      });
      if (!response.ok) throw new Error("Restart request was rejected by the lifecycle supervisor.");
      const { operation_id: operationId } = await response.json();
      if (!operationId) throw new Error("Restart request returned no operation id.");

      const deadline = Date.now() + 120_000;
      while (Date.now() < deadline) {
        await sleep(1000);
        try {
          const operationResponse = await fetch(`/api/supervisor/operations/${encodeURIComponent(operationId)}`, { cache: "no-store" });
          if (!operationResponse.ok) continue;
          const operation = await operationResponse.json();
          if (["FAILED", "PARTIAL", "REJECTED"].includes(operation.state)) {
            throw new Error(operation.detail || `Restart ended in ${operation.state}.`);
          }
          if (operation.state === "SUCCEEDED") {
            await loadSystemStatus();
            return;
          }
        } catch (e) {
          if (e instanceof Error && /Restart ended/.test(e.message)) throw e;
          // Restarting the web component temporarily removes these proxy routes.
          // Retry until the replacement web process is available or the deadline expires.
        }
      }
      throw new Error("Restart did not complete within two minutes.");
    } catch (e) {
      setSystemError(e instanceof Error ? e.message : "Restart failed.");
    } finally {
      setSystemBusy(null);
    }
  }

  async function copyDiagnostics() {
    const payload = {
      captured_at: new Date().toISOString(),
      frontend_mode: frontendMode,
      browser_origin: browserOrigin,
      api_url: API_URL,
      health,
      health_latency_ms: healthLatency,
      backends,
      system_status: systemStatus,
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
          <p className="muted">Connectivity, lifecycle supervision, provider health, and browser-to-API timing.</p>
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
          <div className="diag-label">Lifecycle</div>
          <div className={`diag-value ${systemStatus?.aggregate_state === "HEALTHY" ? "latency-good" : "latency-bad"}`}>{systemStatus?.aggregate_state || "Unavailable"}</div>
          <div className="small muted">Out-of-process local supervisor</div>
        </div>
        <div className="diag-card">
          <div className="diag-label">Active agents</div>
          <div className="diag-value">{health?.active_executions ?? "—"}</div>
          <div className="small muted">Reported by the API</div>
        </div>
      </div>

      <div className="panel">
        <div className="row space-between">
          <div>
            <h2>SceneWorks services</h2>
            <p className="small muted">Lifecycle actions are journaled and limited to semantic API, web, and MCP-tunnel operations.</p>
          </div>
          <button className="btn" disabled={systemBusy !== null || !systemStatus} onClick={() => void restartService("all")}>
            {systemBusy === "all" ? "Restarting…" : "Restart SceneWorks"}
          </button>
        </div>
        {systemError && <div className="notice error">{systemError}</div>}
        {!systemStatus ? (
          <div className="empty">Lifecycle supervisor unavailable.</div>
        ) : (
          <table className="grid">
            <thead><tr><th>Component</th><th>State</th><th>Health failures</th><th>Recovery budget</th><th>Action</th></tr></thead>
            <tbody>
              {(["api", "web", "mcp_tunnel"] as ComponentName[]).map((name) => {
                const component = systemStatus.components[name];
                if (!component) return null;
                return (
                  <tr key={name}>
                    <td>{COMPONENT_LABELS[name]} {!component.enabled && <span className="small muted">(disabled)</span>}</td>
                    <td><span className={`badge ${lifecycleBadge(component.state)}`}>{component.state}</span></td>
                    <td className="mono small">{component.consecutive_failures}/3</td>
                    <td className="mono small">{component.restart_attempts}/3 in 5 min</td>
                    <td>
                      <button className="btn" disabled={!component.enabled || systemBusy !== null} onClick={() => void restartService(name)}>
                        {systemBusy === name ? "Restarting…" : "Restart"}
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      {frontendMode === "development" && (
        <div className="notice">You are running the Next.js development server. First route visits may include compilation latency; use the production launcher for normal operation.</div>
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
        <p className="small muted">Server logs and lifecycle credentials remain outside the browser. The UI receives bounded status and operation metadata only.</p>
      </div>
    </div>
  );
}
