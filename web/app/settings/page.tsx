"use client";

import { useCallback, useEffect, useState } from "react";
import { API_URL, api } from "@/lib/api";
import type { McpMode, McpSettings, ModelProfileRoute, Settings } from "@/lib/types";
import LoadingShell from "@/components/LoadingShell";

const MODE_HELP: Record<McpMode, string> = {
  observe: "Read-only semantic project/task/evidence access. ChatGPT cannot launch agents or change SceneWorks state.",
  standard: "ChatGPT can register projects, create/control governed tasks and ask roles. SceneWorks remains the workflow authority.",
  advanced: "Standard mode plus direct SceneWorks-owned engineering sessions: workspace, commands, processes, Git and optional agent delegation.",
};

const PERMISSION_LABELS: Record<string, string> = {
  repository_read: "Repository read",
  repository_write: "Repository write",
  shell_execute: "Commands / tests",
  process_control: "Persistent process control (PCS, dev servers, logs)",
  git_commit: "Git commit",
  network_access: "Network capability (provider/host dependent)",
  agent_delegate: "Delegate to configured agent backends",
  subagents: "Gemini-native subagents (legacy provider sessions)",
};

const PROFILE_LABELS: Record<string, string> = {
  strongest: "Strongest reasoning",
  coding: "Coding",
  research: "Research",
};

function routeValue(routes: Record<string, ModelProfileRoute>, profile: string): ModelProfileRoute {
  return routes[profile] ?? { backend: null, model: null };
}

export default function SettingsPage() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [mcp, setMcp] = useState<McpSettings | null>(null);
  const [form, setForm] = useState({
    worktree_root: "",
    default_backend: "gemini_acp",
    gemini_executable: "",
    gemini_model: "",
    opencode_executable: "",
    opencode_model: "",
    opencode_agent: "",
    execution_timeout_seconds: "",
  });
  const [routes, setRoutes] = useState<Record<string, ModelProfileRoute>>({});
  const [mcpForm, setMcpForm] = useState({
    enabled: true,
    mode: "observe" as McpMode,
    permissions: [] as string[],
    toolMaxChars: "120000",
  });
  const [saved, setSaved] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [mcpBusy, setMcpBusy] = useState(false);
  const [mcpProbe, setMcpProbe] = useState<string | null>(null);

  const refresh = useCallback(() => {
    Promise.all([api.settings(), api.mcpSettings()])
      .then(([s, m]) => {
        setSettings(s);
        setMcp(m);
        setForm({
          worktree_root: s.worktree_root,
          default_backend: s.default_backend,
          gemini_executable: s.gemini_executable ?? "",
          gemini_model: s.gemini_model ?? "",
          opencode_executable: s.opencode_executable ?? "",
          opencode_model: s.opencode_model ?? "",
          opencode_agent: s.opencode_agent ?? "",
          execution_timeout_seconds: String(s.execution_timeout_seconds),
        });
        setRoutes({ ...s.model_profile_routes });
        setMcpForm({
          enabled: m.enabled,
          mode: m.mode,
          permissions: [...m.advanced_session_permissions],
          toolMaxChars: String(m.tool_max_chars),
        });
      })
      .catch((e) => setError(String(e)));
  }, []);

  useEffect(() => refresh(), [refresh]);

  function updateRoute(profile: string, patch: Partial<ModelProfileRoute>) {
    setRoutes((current) => ({
      ...current,
      [profile]: { ...routeValue(current, profile), ...patch },
    }));
  }

  async function save() {
    setBusy(true);
    setError(null);
    setSaved(null);
    try {
      const normalizedRoutes = Object.fromEntries(
        Object.entries(routes).map(([profile, route]) => [
          profile,
          {
            backend: route.backend || null,
            model: route.model || null,
          },
        ]),
      );
      await api.updateSettings({
        worktree_root: form.worktree_root,
        default_backend: form.default_backend,
        gemini_executable: form.gemini_executable || null,
        gemini_model: form.gemini_model || null,
        opencode_executable: form.opencode_executable || null,
        opencode_model: form.opencode_model || null,
        opencode_agent: form.opencode_agent || null,
        model_profile_routes: normalizedRoutes,
        execution_timeout_seconds: Number(form.execution_timeout_seconds),
      });
      setSaved("Agent, model-routing and operational settings saved.");
      refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function saveMcp() {
    setMcpBusy(true);
    setError(null);
    setSaved(null);
    try {
      await api.updateMcpSettings({
        mcp_enabled: mcpForm.enabled,
        mcp_mode: mcpForm.mode,
        advanced_session_permissions: mcpForm.permissions,
        mcp_tool_max_chars: Number(mcpForm.toolMaxChars),
      });
      setSaved("ChatGPT / MCP settings saved.");
      refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setMcpBusy(false);
    }
  }

  function togglePermission(permission: string) {
    setMcpForm((current) => ({
      ...current,
      permissions: current.permissions.includes(permission)
        ? current.permissions.filter((item) => item !== permission)
        : [...current.permissions, permission],
    }));
  }

  async function testMcp() {
    setMcpProbe("Testing…");
    try {
      const response = await fetch(`${API_URL}/mcp`);
      if (!response.ok) {
        setMcpProbe(`Unavailable (${response.status})`);
        return;
      }
      const body = (await response.json()) as { mode?: string; version?: string };
      setMcpProbe(`Reachable — ${body.mode ?? "unknown mode"}, v${body.version ?? "?"}`);
    } catch {
      setMcpProbe("Unreachable");
    }
  }

  async function copyEndpoint() {
    try {
      await navigator.clipboard.writeText(`${API_URL}/mcp`);
      setMcpProbe("Endpoint copied.");
    } catch {
      setMcpProbe("Could not copy endpoint.");
    }
  }

  if (!settings || !mcp) return <LoadingShell title="Settings" />;

  const selectableBackends = settings.backends.filter((backend) => backend.key !== "fake");

  return (
    <div>
      <h1>Settings</h1>
      <p className="muted">
        SceneWorks separates models, agent backends and the execution runtime. Gemini CLI remains the default worker; direct MCP engineering control does not depend on Gemini being authenticated.
      </p>

      {error && <div className="notice error">{error}</div>}
      {saved && <div className="notice">{saved}</div>}

      <div className="panel">
        <h2>Agent backends</h2>
        <label className="field">
          Default worker
          <select
            value={form.default_backend}
            onChange={(e) => setForm({ ...form, default_backend: e.target.value })}
          >
            {selectableBackends.map((backend) => (
              <option key={backend.key} value={backend.key}>{backend.label}</option>
            ))}
          </select>
          <small>Gemini CLI is the recommended default. This is independent from ChatGPT/MCP direct execution.</small>
        </label>
        <table className="grid">
          <thead>
            <tr>
              <th>Backend</th>
              <th>Status</th>
              <th>Version</th>
              <th>Detail</th>
            </tr>
          </thead>
          <tbody>
            {settings.backends.map((backend) => (
              <tr key={backend.key}>
                <td>
                  <strong>{backend.label}</strong>{" "}
                  <span className="mono small muted">({backend.key})</span>
                </td>
                <td>
                  <span className={`badge ${backend.available ? "success" : "error"}`}>
                    {backend.available ? "Available" : "Unavailable"}
                  </span>
                </td>
                <td className="mono small">{backend.version ?? "—"}</td>
                <td className="small muted">{backend.detail ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="panel">
        <h2>Model routing</h2>
        <p className="small muted">
          Roles request provider-neutral profiles. A route may select a different backend and model without changing role definitions. Empty values inherit the role/backend defaults.
        </p>
        <table className="grid">
          <thead>
            <tr>
              <th>Profile</th>
              <th>Backend</th>
              <th>Model override</th>
            </tr>
          </thead>
          <tbody>
            {Object.keys(PROFILE_LABELS).map((profile) => {
              const route = routeValue(routes, profile);
              return (
                <tr key={profile}>
                  <td><strong>{PROFILE_LABELS[profile]}</strong><br /><span className="mono small muted">{profile}</span></td>
                  <td>
                    <select
                      value={route.backend ?? ""}
                      onChange={(e) => updateRoute(profile, { backend: e.target.value || null })}
                    >
                      <option value="">Inherit role/default</option>
                      {selectableBackends.map((backend) => (
                        <option key={backend.key} value={backend.key}>{backend.label}</option>
                      ))}
                    </select>
                  </td>
                  <td>
                    <input
                      value={route.model ?? ""}
                      onChange={(e) => updateRoute(profile, { model: e.target.value || null })}
                      placeholder="inherit backend default"
                    />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="panel">
        <h2>Gemini CLI — default ACP worker</h2>
        <label className="field">
          Executable
          <input
            value={form.gemini_executable}
            onChange={(e) => setForm({ ...form, gemini_executable: e.target.value })}
            placeholder="gemini"
          />
          <small>Leave empty to auto-discover on PATH. Authentication remains owned by Gemini CLI.</small>
        </label>
        <label className="field">
          Default model override (optional)
          <input
            value={form.gemini_model}
            onChange={(e) => setForm({ ...form, gemini_model: e.target.value })}
            placeholder="leave empty for Gemini CLI selection"
          />
        </label>
        <p className="small muted">
          Gemini-specific web/search/subagent capabilities remain inside this backend. SceneWorks does not duplicate them in the native runtime.
        </p>
      </div>

      <div className="panel">
        <h2>OpenCode — non-ACP backup worker</h2>
        <label className="field">
          Executable
          <input
            value={form.opencode_executable}
            onChange={(e) => setForm({ ...form, opencode_executable: e.target.value })}
            placeholder="opencode"
          />
          <small>Leave empty to auto-discover on PATH. SceneWorks uses OpenCode headless CLI, not ACP.</small>
        </label>
        <label className="field">
          Default provider/model (optional)
          <input
            value={form.opencode_model}
            onChange={(e) => setForm({ ...form, opencode_model: e.target.value })}
            placeholder="provider/model"
          />
          <small>OpenCode owns provider credentials and provider configuration.</small>
        </label>
        <label className="field">
          Agent profile (optional)
          <input
            value={form.opencode_agent}
            onChange={(e) => setForm({ ...form, opencode_agent: e.target.value })}
            placeholder="OpenCode default agent"
          />
        </label>
        <p className="small muted">
          WP14 qualifies this adapter for write-capable coding/delegation work. Read-only roles stay on backends with enforceable read-only tooling until an OpenCode policy adapter is added.
        </p>
      </div>

      <div className="panel">
        <h2>ChatGPT / MCP</h2>
        <label className="field">
          <span>
            <input
              type="checkbox"
              checked={mcpForm.enabled}
              onChange={(e) => setMcpForm({ ...mcpForm, enabled: e.target.checked })}
            />{" "}
            Enable SceneWorks MCP server
          </span>
          <small>Endpoint: <span className="mono">{API_URL}/mcp</span></small>
        </label>

        <label className="field">
          Operating mode
          <select
            value={mcpForm.mode}
            onChange={(e) => setMcpForm({ ...mcpForm, mode: e.target.value as McpMode })}
          >
            <option value="observe">Observe — read only</option>
            <option value="standard">Standard — governed SceneWorks actions</option>
            <option value="advanced">Advanced — direct engineering control</option>
          </select>
          <small>{MODE_HELP[mcpForm.mode]}</small>
        </label>

        {mcpForm.mode === "advanced" && (
          <div className="panel">
            <h3>Direct engineering capability ceiling</h3>
            <p className="small muted">
              Maximum permissions an MCP EngineeringSession may request. Runtime: {mcp.available_runtimes.join(", ") || "none"}. Delegated workers: {mcp.available_backends.join(", ") || "none"}.
            </p>
            {mcp.available_advanced_permissions.map((permission) => (
              <label className="field" key={permission}>
                <span>
                  <input
                    type="checkbox"
                    checked={mcpForm.permissions.includes(permission)}
                    onChange={() => togglePermission(permission)}
                  />{" "}
                  {PERMISSION_LABELS[permission] ?? permission}
                </span>
              </label>
            ))}
            <div className="notice error">{mcp.advanced_warning}</div>
          </div>
        )}

        <label className="field">
          Maximum text returned by one MCP tool
          <input
            type="number"
            min={10000}
            max={1000000}
            value={mcpForm.toolMaxChars}
            onChange={(e) => setMcpForm({ ...mcpForm, toolMaxChars: e.target.value })}
          />
        </label>

        <div>
          <button className="btn primary" onClick={saveMcp} disabled={mcpBusy}>
            {mcpBusy ? "Saving…" : "Save ChatGPT / MCP settings"}
          </button>{" "}
          <button className="btn" onClick={testMcp}>Test MCP endpoint</button>{" "}
          <button className="btn" onClick={copyEndpoint}>Copy endpoint</button>
        </div>
        {mcpProbe && <p className="small muted">{mcpProbe}</p>}
        <p className="small muted">
          Standard mode can register a host-visible repository and control governed work. Advanced mode lets ChatGPT create its own isolated worktree and operate it directly; Gemini authentication is not required for those native tools.
        </p>
      </div>

      <div className="panel">
        <h2>Workspace & limits</h2>
        <label className="field">
          Worktree root
          <input
            value={form.worktree_root}
            onChange={(e) => setForm({ ...form, worktree_root: e.target.value })}
          />
          <small>Where SceneWorks creates isolated task and MCP EngineeringSession worktrees. Must be outside managed repositories.</small>
        </label>
        <label className="field">
          Execution timeout (seconds)
          <input
            type="number"
            value={form.execution_timeout_seconds}
            onChange={(e) => setForm({ ...form, execution_timeout_seconds: e.target.value })}
          />
        </label>
        <button className="btn primary" onClick={save} disabled={busy}>
          {busy ? "Saving…" : "Save agent & operational settings"}
        </button>
      </div>

      <div className="panel">
        <h3>Read-only environment values</h3>
        <table className="grid">
          <tbody>
            <tr>
              <td className="muted">Log level</td>
              <td className="mono">{settings.log_level}</td>
            </tr>
            <tr>
              <td className="muted">Context limit</td>
              <td className="mono">{settings.context_max_bytes} bytes</td>
            </tr>
            <tr>
              <td className="muted">Database</td>
              <td className="mono">{settings.database_url}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  );
}
