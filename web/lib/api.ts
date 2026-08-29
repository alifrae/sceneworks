// Minimal typed fetch client for the SceneWorks API.

import type {
  AppEvent,
  Artifact,
  Backend,
  Dashboard,
  Diff,
  EngineeringContract,
  Execution,
  Initiative,
  McpSettings,
  Project,
  ProjectMemory,
  ProjectProvenance,
  RepoStatus,
  Role,
  RoutingSettings,
  Settings,
  Task,
  TaskProvenance,
  TaskVerificationView,
  WorkPackage,
} from "./types";

// The backend binds IPv4 loopback by default. Keep that exact address rather
// than deriving localhost from the frontend host, which could resolve to ::1
// on some Windows setups. Remote deployments should set NEXT_PUBLIC_API_URL.
export const API_URL: string =
  process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8010";

export class ApiError extends Error {
  status: number;
  requestId: string;
  durationMs: number;

  constructor(status: number, message: string, requestId = "", durationMs = 0) {
    super(message);
    this.status = status;
    this.requestId = requestId;
    this.durationMs = durationMs;
  }
}

export type ApiHealth = {
  status: string;
  app: string;
  active_executions: number;
};

export type RequestDiagnostic = {
  timestamp: string;
  method: string;
  path: string;
  status: number;
  requestId: string;
  durationMs: number;
  serverDurationMs: number | null;
  responseSize: string | null;
  cause: string;
};

const REQUEST_DIAGNOSTICS: RequestDiagnostic[] = [];
const MAX_REQUEST_DIAGNOSTICS = 60;

export function getRequestDiagnostics(): RequestDiagnostic[] {
  return [...REQUEST_DIAGNOSTICS].reverse();
}

function unreachableMessage(cause?: unknown): string {
  const detail = cause instanceof Error && cause.message ? ` (${cause.name}: ${cause.message})` : "";
  return `the SceneWorks API at ${API_URL} did not respond${detail}. Is the backend running?`;
}

export function errorMessage(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

type RequestOptions = {
  cacheTtlMs?: number;
  diagnosticCause?: string;
  bypassCache?: boolean;
  retryGet?: boolean;
};
type CacheEntry = { value: unknown; expiresAt: number };
const GET_CACHE = new Map<string, CacheEntry>();
const GET_INFLIGHT = new Map<string, Promise<unknown>>();
const DEFAULT_CACHE_TTL_MS = 2_000;

function requestId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `sw-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function now(): number {
  return typeof performance !== "undefined" ? performance.now() : Date.now();
}

function isGet(init?: RequestInit): boolean {
  return !init?.method || init.method.toUpperCase() === "GET";
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function invalidateAfterMutation(path: string): void {
  const resource = path.split("?")[0];
  const prefixes = resource.startsWith("/api/tasks")
    ? ["/api/tasks", "/api/projects", "/api/initiatives", "/api/work-packages", "/api/dashboard"]
    : resource.startsWith("/api/projects") || resource.startsWith("/api/initiatives") || resource.startsWith("/api/work-packages")
      ? ["/api/projects", "/api/initiatives", "/api/work-packages", "/api/tasks", "/api/dashboard"]
      : resource.startsWith("/api/executions") || resource.startsWith("/api/company")
        ? ["/api/executions", "/api/company", "/api/dashboard"]
        : resource.startsWith("/api/settings") || resource.startsWith("/api/backends")
          ? ["/api/settings", "/api/roles", "/api/company", "/api/backends"]
          : [resource];
  for (const key of GET_CACHE.keys()) {
    if (prefixes.some((prefix) => key === prefix || key.startsWith(`${prefix}?`) || key.startsWith(`${prefix}/`))) {
      GET_CACHE.delete(key);
    }
  }
}

function diagnostics(meta: RequestDiagnostic): void {
  REQUEST_DIAGNOSTICS.push(meta);
  if (REQUEST_DIAGNOSTICS.length > MAX_REQUEST_DIAGNOSTICS) {
    REQUEST_DIAGNOSTICS.splice(0, REQUEST_DIAGNOSTICS.length - MAX_REQUEST_DIAGNOSTICS);
  }
  if (process.env.NODE_ENV === "development") console.debug("[SceneWorks request]", meta);
}

async function request<T>(path: string, init?: RequestInit, options: RequestOptions = {}): Promise<T> {
  const get = isGet(init);
  const cacheKey = path;
  const ttl = options.cacheTtlMs ?? DEFAULT_CACHE_TTL_MS;
  if (get && !options.bypassCache) {
    const cached = GET_CACHE.get(cacheKey);
    if (cached && cached.expiresAt > Date.now()) {
      diagnostics({
        timestamp: new Date().toISOString(),
        method: "GET",
        path,
        status: 200,
        requestId: "cache",
        durationMs: 0,
        serverDurationMs: null,
        responseSize: null,
        cause: "cache-hit",
      });
      return cached.value as T;
    }
    const inFlight = GET_INFLIGHT.get(cacheKey);
    if (inFlight) return inFlight as Promise<T>;
  }

  const id = requestId();
  const started = now();
  const method = init?.method?.toUpperCase() ?? "GET";
  const promise = (async () => {
    let response: Response | undefined;
    let lastCause: unknown;
    const attempts = get && options.retryGet !== false ? 2 : 1;

    for (let attempt = 0; attempt < attempts; attempt += 1) {
      try {
        const headers = new Headers(init?.headers);
        if (!get) {
          if (init?.body !== undefined && !headers.has("Content-Type")) {
            headers.set("Content-Type", "application/json");
          }
          headers.set("X-Request-ID", id);
        }
        response = await fetch(`${API_URL}${path}`, { ...init, headers });
        break;
      } catch (cause) {
        lastCause = cause;
        if (attempt + 1 < attempts) await sleep(250);
      }
    }

    if (!response) {
      const durationMs = Math.round(now() - started);
      diagnostics({
        timestamp: new Date().toISOString(),
        method,
        path,
        status: 0,
        requestId: id,
        durationMs,
        serverDurationMs: null,
        responseSize: null,
        cause: options.diagnosticCause ?? "transport-failure",
      });
      throw new ApiError(0, unreachableMessage(lastCause), id, durationMs);
    }

    const durationMs = Math.round(now() - started);
    const serverDurationHeader = response.headers.get("x-process-time-ms");
    diagnostics({
      timestamp: new Date().toISOString(),
      method,
      path,
      status: response.status,
      requestId: response.headers.get("x-request-id") || id,
      durationMs,
      serverDurationMs: serverDurationHeader ? Number(serverDurationHeader) : null,
      responseSize: response.headers.get("content-length"),
      cause: options.diagnosticCause ?? (get ? "cache-miss" : "mutation"),
    });
    if (!response.ok) {
      let detail = response.statusText;
      try {
        const body = await response.json();
        detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
      } catch {
        /* keep statusText */
      }
      throw new ApiError(response.status, detail, response.headers.get("x-request-id") || id, durationMs);
    }
    if (response.status === 204) return undefined as T;
    const value = (await response.json()) as T;
    if (get && ttl > 0) GET_CACHE.set(cacheKey, { value, expiresAt: Date.now() + ttl });
    return value;
  })();

  if (get && !options.bypassCache) {
    GET_INFLIGHT.set(cacheKey, promise);
    promise.finally(() => GET_INFLIGHT.delete(cacheKey)).catch(() => undefined);
  } else if (!get) {
    invalidateAfterMutation(path);
  }
  return promise;
}

export const api = {
  health: () => request<ApiHealth>("/api/health", undefined, {
    cacheTtlMs: 0,
    bypassCache: true,
    diagnosticCause: "diagnostics-health",
  }),
  dashboard: () => request<Dashboard>("/api/dashboard", undefined, { diagnosticCause: "dashboard-load" }),

  projects: () => request<Project[]>("/api/projects", undefined, { diagnosticCause: "projects-load", cacheTtlMs: 5_000 }),
  project: (id: number) => request<Project>(`/api/projects/${id}`),
  createProject: (body: Record<string, unknown>) => request<Project>("/api/projects", { method: "POST", body: JSON.stringify(body) }),
  updateProject: (id: number, body: Record<string, unknown>) => request<Project>(`/api/projects/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteProject: (id: number, purgeHistory = false, force = false) => {
    const query = new URLSearchParams();
    if (purgeHistory) query.set("purge_history", "true");
    if (force) query.set("force", "true");
    const suffix = query.toString();
    return request<void>(`/api/projects/${id}${suffix ? `?${suffix}` : ""}`, { method: "DELETE" });
  },
  projectStatus: (id: number) => request<RepoStatus>(`/api/projects/${id}/status`),
  projectProvenance: (id: number, path?: string) => {
    const query = path ? `?${new URLSearchParams({ path }).toString()}` : "";
    return request<ProjectProvenance>(`/api/projects/${id}/provenance${query}`);
  },

  initiatives: (projectId: number) => request<Initiative[]>(`/api/projects/${projectId}/initiatives`),
  createInitiative: (projectId: number, body: Record<string, unknown>) =>
    request<Initiative>(`/api/projects/${projectId}/initiatives`, { method: "POST", body: JSON.stringify(body) }),
  initiative: (id: number) => request<Initiative>(`/api/initiatives/${id}`),
  updateInitiative: (id: number, body: Record<string, unknown>) =>
    request<Initiative>(`/api/initiatives/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  workPackages: (initiativeId: number) => request<WorkPackage[]>(`/api/initiatives/${initiativeId}/work-packages`),
  createWorkPackage: (initiativeId: number, body: Record<string, unknown>) =>
    request<WorkPackage>(`/api/initiatives/${initiativeId}/work-packages`, { method: "POST", body: JSON.stringify(body) }),
  updateWorkPackage: (id: number, body: Record<string, unknown>) =>
    request<WorkPackage>(`/api/work-packages/${id}`, { method: "PATCH", body: JSON.stringify(body) }),

  tasks: (params?: Record<string, string>) => {
    const query = new URLSearchParams(params ?? {}).toString();
    return request<Task[]>(`/api/tasks${query ? `?${query}` : ""}`, undefined, { diagnosticCause: "tasks-load" });
  },
  task: (id: number) => request<Task>(`/api/tasks/${id}`),
  taskVerification: (id: number) => request<TaskVerificationView>(`/api/tasks/${id}/verification`, undefined, { cacheTtlMs: 0, bypassCache: true }),
  createTask: (body: Record<string, unknown>) => request<Task>("/api/tasks", { method: "POST", body: JSON.stringify(body) }),
  replaceTaskContract: (id: number, contract: EngineeringContract) => request<Task>(`/api/tasks/${id}/contract`, { method: "PUT", body: JSON.stringify(contract) }),
  taskProvenance: (id: number) => request<TaskProvenance>(`/api/tasks/${id}/provenance`),
  taskEvents: (id: number) => request<AppEvent[]>(`/api/tasks/${id}/events`),
  taskDiff: (id: number) => request<Diff>(`/api/tasks/${id}/diff`),
  taskAction: (id: number, action: string, body?: Record<string, string>) => request<Task>(`/api/tasks/${id}/actions/${action.replaceAll("_", "-")}`, { method: "POST", body: JSON.stringify(body ?? {}) }),

  executions: (params?: Record<string, string>) => {
    const query = new URLSearchParams(params ?? {}).toString();
    return request<Execution[]>(`/api/executions${query ? `?${query}` : ""}`, undefined, { diagnosticCause: "executions-load" });
  },
  execution: (id: string) => request<Execution>(`/api/executions/${id}`),
  executionEvents: (id: string) => request<AppEvent[]>(`/api/executions/${id}/events`),
  cancelExecution: (id: string) => request<{ cancelled: boolean }>(`/api/executions/${id}/cancel`, { method: "POST" }),

  roles: () => request<Role[]>("/api/roles", undefined, { cacheTtlMs: 60_000 }),
  companyRoles: () => request<Role[]>("/api/company/roles", undefined, { cacheTtlMs: 60_000 }),
  companyAsk: (body: { role: string; project_id: number | null; question: string }) => request<Execution>("/api/company/ask", { method: "POST", body: JSON.stringify(body) }),
  artifacts: () => request<Artifact[]>("/api/company/artifacts", undefined, { cacheTtlMs: 4_000 }),

  backends: (refresh = false) => request<Backend[]>(`/api/backends${refresh ? "?refresh=true" : ""}`, undefined, {
    cacheTtlMs: refresh ? 0 : 2_000,
    bypassCache: refresh,
    diagnosticCause: refresh ? "backend-health-refresh" : "backend-health-load",
  }),
  settings: () => request<Settings>("/api/settings", undefined, { cacheTtlMs: 30_000 }),
  updateSettings: (body: Record<string, unknown>) => request<Settings>("/api/settings", { method: "PATCH", body: JSON.stringify(body) }),
  routingSettings: () => request<RoutingSettings>("/api/settings/routing", undefined, { cacheTtlMs: 0, bypassCache: true }),
  updateRoutingSettings: (body: { role_profile_overrides: Record<string, string> }) =>
    request<RoutingSettings>("/api/settings/routing", { method: "PATCH", body: JSON.stringify(body) }),
  mcpSettings: () => request<McpSettings>("/api/settings/mcp", undefined, { cacheTtlMs: 5_000 }),
  updateMcpSettings: (body: Record<string, unknown>) => request<McpSettings>("/api/settings/mcp", { method: "PATCH", body: JSON.stringify(body) }),

  memoryList: (projectId: number, params?: Record<string, string>) => {
    const query = new URLSearchParams(params ?? {}).toString();
    return request<ProjectMemory[]>(`/api/projects/${projectId}/memory${query ? `?${query}` : ""}`);
  },
  memoryCreate: (projectId: number, body: Record<string, unknown>) => request<ProjectMemory>(`/api/projects/${projectId}/memory`, { method: "POST", body: JSON.stringify(body) }),
  memoryUpdate: (projectId: number, memoryId: number, body: Record<string, unknown>) => request<ProjectMemory>(`/api/projects/${projectId}/memory/${memoryId}`, { method: "PATCH", body: JSON.stringify(body) }),
  memoryArchive: (projectId: number, memoryId: number) => request<ProjectMemory>(`/api/projects/${projectId}/memory/${memoryId}/archive`, { method: "POST" }),
  memorySupersede: (projectId: number, memoryId: number, replacementId: number) => request<{ superseded: ProjectMemory; replacement: ProjectMemory }>(`/api/projects/${projectId}/memory/${memoryId}/supersede?replacement_id=${replacementId}`, { method: "POST" }),
};

export function eventsUrl(taskId?: number, executionId?: string): string {
  const params = new URLSearchParams();
  if (taskId !== undefined) params.set("task_id", String(taskId));
  if (executionId) params.set("execution_id", executionId);
  const query = params.toString();
  return `${API_URL}/api/events/stream${query ? `?${query}` : ""}`;
}
