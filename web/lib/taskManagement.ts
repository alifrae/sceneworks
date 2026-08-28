import { API_URL, ApiError } from "./api";
import type { ExecutionMode, Task, WorkItemType } from "./types";

export type BacklogTaskUpdate = {
  priority?: "low" | "medium" | "high";
  work_item_type?: WorkItemType;
  requested_mode?: ExecutionMode;
};

export async function updateBacklogTask(id: number, body: BacklogTaskUpdate): Promise<Task> {
  let response: Response;
  try {
    response = await fetch(`${API_URL}/api/tasks/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (cause) {
    throw new ApiError(0, cause instanceof Error ? cause.message : "SceneWorks API did not respond");
  }
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const payload = await response.json();
      detail = typeof payload.detail === "string" ? payload.detail : JSON.stringify(payload.detail ?? payload);
    } catch {
      /* keep status text */
    }
    throw new ApiError(response.status, detail);
  }
  return response.json() as Promise<Task>;
}
