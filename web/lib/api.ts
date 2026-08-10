// Minimal typed fetch client for the SceneWorks API.

import type {
  AppEvent,
  Artifact,
  Backend,
  Dashboard,
  Diff,
  Execution,
  Project,
  ProjectMemory,
  RepoStatus,
  Role,
  Settings,
  Task,
} from "./types";

export const API_URL: string =
  process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8010";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
    } catch {
      /* keep statusText */
    }
    throw new ApiError(response.status, detail);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  // dashboard
  dashboard: () => request<Dashboard>("/api/dashboard"),

  // projects
  projects: () => request<Project[]>("/api/projects"),
  project: (id: number) => request<Project>(`/api/projects/${id}`),
  createProject: (body: Record<string, unknown>) =>
    request<Project>("/api/projects", { method: "POST", body: JSON.stringify(body) }),
  updateProject: (id: number, body: Record<string, unknown>) =>
    request<Project>(`/api/projects/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  projectStatus: (id: number) => request<RepoStatus>(`/api/projects/${id}/status`),

  // tasks
  tasks: (params?: Record<string, string>) => {
    const query = new URLSearchParams(params ?? {}).toString();
    return request<Task[]>(`/api/tasks${query ? `?${query}` : ""}`);
  },
  task: (id: number) => request<Task>(`/api/tasks/${id}`),
  createTask: (body: Record<string, unknown>) =>
    request<Task>("/api/tasks", { method: "POST", body: JSON.stringify(body) }),
  taskEvents: (id: number) => request<AppEvent[]>(`/api/tasks/${id}/events`),
  taskDiff: (id: number) => request<Diff>(`/api/tasks/${id}/diff`),
  taskAction: (id: number, action: string, body?: Record<string, string>) =>
    request<Task>(`/api/tasks/${id}/actions/${action}`, {
      method: "POST",
      body: JSON.stringify(body ?? {}),
    }),

  // executions
  executions: (params?: Record<string, string>) => {
    const query = new URLSearchParams(params ?? {}).toString();
    return request<Execution[]>(`/api/executions${query ? `?${query}` : ""}`);
  },
  execution: (id: string) => request<Execution>(`/api/executions/${id}`),
  executionEvents: (id: string) => request<AppEvent[]>(`/api/executions/${id}/events`),
  cancelExecution: (id: string) =>
    request<{ cancelled: boolean }>(`/api/executions/${id}/cancel`, { method: "POST" }),

  // company
  roles: () => request<Role[]>("/api/roles"),
  companyRoles: () => request<Role[]>("/api/company/roles"),
  companyAsk: (body: { role: string; project_id: number | null; question: string }) =>
    request<Execution>("/api/company/ask", { method: "POST", body: JSON.stringify(body) }),
  artifacts: () => request<Artifact[]>("/api/company/artifacts"),

  // system
  backends: () => request<Backend[]>("/api/backends"),
  settings: () => request<Settings>("/api/settings"),
  updateSettings: (body: Record<string, unknown>) =>
    request<Settings>("/api/settings", { method: "PATCH", body: JSON.stringify(body) }),

  // memory (V2.4)
  memoryList: (projectId: number, params?: Record<string, string>) => {
    const query = new URLSearchParams(params ?? {}).toString();
    return request<ProjectMemory[]>(
      `/api/projects/${projectId}/memory${query ? `?${query}` : ""}`,
    );
  },
  memoryCreate: (projectId: number, body: Record<string, unknown>) =>
    request<ProjectMemory>(`/api/projects/${projectId}/memory`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  memoryUpdate: (projectId: number, memoryId: number, body: Record<string, unknown>) =>
    request<ProjectMemory>(`/api/projects/${projectId}/memory/${memoryId}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  memoryArchive: (projectId: number, memoryId: number) =>
    request<ProjectMemory>(`/api/projects/${projectId}/memory/${memoryId}/archive`, {
      method: "POST",
    }),
  memorySupersede: (projectId: number, memoryId: number, replacementId: number) =>
    request<{ superseded: ProjectMemory; replacement: ProjectMemory }>(
      `/api/projects/${projectId}/memory/${memoryId}/supersede?replacement_id=${replacementId}`,
      { method: "POST" },
    ),
};

export function eventsUrl(taskId?: number, executionId?: string): string {
  const params = new URLSearchParams();
  if (taskId !== undefined) params.set("task_id", String(taskId));
  if (executionId) params.set("execution_id", executionId);
  const query = params.toString();
  return `${API_URL}/api/events/stream${query ? `?${query}` : ""}`;
}
