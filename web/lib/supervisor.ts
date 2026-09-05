import "server-only";

import { readFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import {
  normalizeSupervisorStatus,
  validateSupervisorComponent,
} from "./supervisor-public.mjs";

const SUPERVISOR_URL = "http://127.0.0.1:8020";

function tokenPath() {
  if (process.platform === "win32" && process.env.LOCALAPPDATA) {
    return path.join(process.env.LOCALAPPDATA, "SceneWorks", "supervisor", "token");
  }
  if (process.env.XDG_DATA_HOME) {
    return path.join(process.env.XDG_DATA_HOME, "sceneworks", "supervisor", "token");
  }
  return path.join(os.homedir(), ".local", "share", "sceneworks", "supervisor", "token");
}

async function supervisorToken() {
  const envToken = process.env.SCENEWORKS_SUPERVISOR_TOKEN?.trim();
  if (envToken) return envToken;
  const value = (await readFile(tokenPath(), "utf8")).trim();
  if (!value) throw new Error("local supervisor token is unavailable");
  return value;
}

async function decode(response: Response) {
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof payload?.error === "string" ? payload.error : `supervisor HTTP ${response.status}`;
    throw new Error(detail);
  }
  return payload;
}

export async function getSupervisorStatus() {
  const response = await fetch(`${SUPERVISOR_URL}/v1/status`, {
    cache: "no-store",
    signal: AbortSignal.timeout(2500),
  });
  return normalizeSupervisorStatus(await decode(response));
}

export async function getSupervisorOperation(operationId: string) {
  if (!/^[A-Za-z0-9-]{1,80}$/.test(operationId)) {
    throw new Error("invalid operation id");
  }
  const response = await fetch(`${SUPERVISOR_URL}/v1/operations/${encodeURIComponent(operationId)}`, {
    cache: "no-store",
    signal: AbortSignal.timeout(2500),
  });
  const payload = await decode(response);
  return {
    operation_id: String(payload.operation_id || operationId),
    actor: String(payload.actor || ""),
    action: String(payload.action || ""),
    component: payload.component == null ? null : String(payload.component),
    state: String(payload.state || "UNKNOWN"),
    accepted_at: Number.isFinite(payload.accepted_at) ? Number(payload.accepted_at) : null,
    started_at: Number.isFinite(payload.started_at) ? Number(payload.started_at) : null,
    finished_at: Number.isFinite(payload.finished_at) ? Number(payload.finished_at) : null,
    detail: payload.detail == null ? null : String(payload.detail).slice(0, 512),
  };
}

export async function restartSupervisor(component: string) {
  const semanticComponent = validateSupervisorComponent(component);
  const token = await supervisorToken();
  const all = semanticComponent === "all";
  const response = await fetch(
    `${SUPERVISOR_URL}/v1/actions/${all ? "restart-all" : "restart"}`,
    {
      method: "POST",
      cache: "no-store",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
        "X-SceneWorks-Actor": "user_ui",
      },
      body: JSON.stringify(all ? {} : { component: semanticComponent }),
      signal: AbortSignal.timeout(5000),
    },
  );
  const payload = await decode(response);
  return { operation_id: String(payload.operation_id || "") };
}

export { normalizeSupervisorStatus, validateSupervisorComponent };
