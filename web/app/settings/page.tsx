"use client";

import { useCallback, useEffect, useState } from "react";
import { API_URL, api } from "@/lib/api";
import type { McpMode, McpSettings, Settings } from "@/lib/types";
import LoadingShell from "@/components/LoadingShell";

const MODE_HELP: Record<McpMode, string> = {
  observe: "Read-only semantic project/task/evidence access. ChatGPT cannot launch agents or change SceneWorks state.",
  standard: "ChatGPT can create/control governed SceneWorks tasks and ask roles. SceneWorks remains the workflow authority.",
  advanced: "Standard mode plus persistent Gemini ACP sessions supervised iteratively by ChatGPT in isolated worktrees.",
};

const PERMISSION_LABELS: Record<string, string> = {
  repository_read: "Repository read",
  repository_write: "Repository write",
  shell_execute: "Shell / tests",
  git_commit: "Git commit",
  network_access: "Web / network",
  subagents: "Gemini native subagents",
};

export default function SettingsPage() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [mcp, setMcp] = useState<McpSettings | null>(null);
  const [form, setForm] = useState({
    worktree_root: "",
    gemini_executable: "",
    gemini_model: "",
    execution_timeout_seconds: "",
  });
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
          gemini_executable: s.gemini_executable ?? "",
          gemini_model: s.gemini_model ?? "",
          execution_timeout_seconds: String(s.execution_timeout_seconds),
        });
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

  async function save() {
    setBusy(true);
    setError(null);
    setSaved(null);
    try {
      await api.updateSettings({
        worktree_root: form.worktree_root,
        gemini_executable: form.gemini_executable || null,
        gemini_model: form.gemini_model || null,
        execution_timeout_seconds: Number(form.execution_timeout_seconds),
      });
      setSaved("Operational settings saved.");
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

  return (
    <div>
      <h1>Settings</h1>
      <p className="muted">
        Operational configuration. Secrets are never stored or shown here — Gemini CLI uses its own authentication outside SceneWorks.
      </p>

      {error && <div className="notice error">{error}</div>}
      {saved && <div className="notice">{saved}</div>}

      <div className="panel">
        <h2>Agent backends</h2>
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
        <h2>Gemini CLI (ACP backend)</h2>
        <label className="field">
          Executable
          <input
            value={form.gemini_executable}
            onChange={(e) => setForm({ ...form, gemini_executable: e.target.value })}
            placeholder="gemini"
          />
          <small>Leave empty to auto-discover on PATH. Overrides SCENEWORKS_GEMINI_EXECUTABLE.</small>
        </label>
        <label className="field">
          Model override (optional)
          <input
            value={form.gemini_model}
            onChange={(e) => setForm({ ...form, gemini_model: e.target.value })}
            placeholder="leave empty for automatic selection"
          />
          <small>
            Governed roles and newly created Advanced sessions inherit this unless a more specific model route/session model is supplied.
          </small>
        </label>
        <p className="small muted">
          SceneWorks mediates ACP file reads/writes and terminal creation. Gemini-native web search/fetch and subagents remain provider capabilities and are version-dependent. Advanced-session creation records the capabilities advertised by the installed CLI.
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
            <option value="advanced">Advanced — ChatGPT supervises Gemini</option>
          </select>
          <small>{MODE_HELP[mcpForm.mode]}</small>
        </label>

        {mcpForm.mode === "advanced" && (
          <div className="panel">
            <h3>Advanced-session capability ceiling</h3>
            <p className="small muted">
              These permissions are the maximum ChatGPT may request for a Gemini session. Individual sessions can use a smaller subset.
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
            <div className="notice error">
              {mcp.advanced_warning}
            </div>
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
          Start in Observe mode when connecting a new ChatGPT plugin/tunnel. Move to Standard after read tools work. Enable Advanced only when you intentionally want ChatGPT to supervise Gemini CLI execution sessions.
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
          <small>Where SceneWorks creates isolated Git worktrees. Must be outside managed repositories.</small>
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
          {busy ? "Saving…" : "Save operational settings"}
        </button>
      </div>

      <div className="panel">
        <h3>Read-only environment values</h3>
        <table className="grid">
          <tbody>
            <tr>
              <td className="muted">Default backend</td>
              <td className="mono">{settings.default_backend}</td>
            </tr>
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
