"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Settings } from "@/lib/types";

export default function SettingsPage() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [form, setForm] = useState({ worktree_root: "", gemini_executable: "", gemini_model: "", execution_timeout_seconds: "" });
  const [saved, setSaved] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(() => {
    api.settings().then((s) => {
      setSettings(s);
      setForm({
        worktree_root: s.worktree_root,
        gemini_executable: s.gemini_executable ?? "",
        gemini_model: s.gemini_model ?? "",
        execution_timeout_seconds: String(s.execution_timeout_seconds),
      });
    }).catch((e) => setError(String(e)));
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
      setSaved("Settings saved.");
      refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  if (!settings) return <div className="empty">Loading…</div>;

  return (
    <div>
      <h1>Settings</h1>
      <p className="muted">
        Operational configuration. Secrets are never stored or shown here — the Gemini CLI uses
        its own authentication outside SceneWorks.
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
                  <strong>{backend.label}</strong> <span className="mono small muted">({backend.key})</span>
                </td>
                <td>
                  <span className={`badge`} style={{ background: backend.available ? "#22c55e" : "#ef4444" }}>
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
          <input value={form.gemini_executable} onChange={(e) => setForm({ ...form, gemini_executable: e.target.value })} placeholder="gemini" />
          <small>Leave empty to auto-discover on PATH. Overrides SCENEWORKS_GEMINI_EXECUTABLE.</small>
        </label>
        <label className="field">
          Model preference (optional)
          <input value={form.gemini_model} onChange={(e) => setForm({ ...form, gemini_model: e.target.value })} placeholder="e.g. gemini-2.5-pro" />
        </label>
      </div>

      <div className="panel">
        <h2>Workspace & limits</h2>
        <label className="field">
          Worktree root
          <input value={form.worktree_root} onChange={(e) => setForm({ ...form, worktree_root: e.target.value })} />
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
          {busy ? "Saving…" : "Save settings"}
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
