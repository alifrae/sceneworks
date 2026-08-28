import { API_URL } from "@/lib/api";
import type { TaskAttachment } from "@/lib/types";

const MAX_ATTACHMENT_BYTES = 20_000_000;

async function responseError(response: Response): Promise<Error> {
  let detail = response.statusText || `HTTP ${response.status}`;
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string") detail = body.detail;
  } catch {
    // Keep status text.
  }
  return new Error(detail);
}

export function fileToBase64(file: File): Promise<string> {
  if (file.size > MAX_ATTACHMENT_BYTES) {
    return Promise.reject(new Error(`${file.name} exceeds the 20 MB attachment limit.`));
  }
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error ?? new Error(`Could not read ${file.name}`));
    reader.onload = () => {
      const result = String(reader.result ?? "");
      const comma = result.indexOf(",");
      if (comma < 0) reject(new Error(`Could not encode ${file.name}`));
      else resolve(result.slice(comma + 1));
    };
    reader.readAsDataURL(file);
  });
}

export async function uploadTaskAttachment(taskId: number, file: File): Promise<TaskAttachment> {
  const data_base64 = await fileToBase64(file);
  const response = await fetch(`${API_URL}/api/tasks/${taskId}/attachments`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ filename: file.name, data_base64, source: "web" }),
  });
  if (!response.ok) throw await responseError(response);
  return (await response.json()) as TaskAttachment;
}

export async function listTaskAttachments(taskId: number): Promise<TaskAttachment[]> {
  const response = await fetch(`${API_URL}/api/tasks/${taskId}/attachments`);
  if (!response.ok) throw await responseError(response);
  return (await response.json()) as TaskAttachment[];
}

export function taskAttachmentUrl(taskId: number, attachmentId: number, download = false): string {
  return `${API_URL}/api/tasks/${taskId}/attachments/${attachmentId}/content${download ? "?download=true" : ""}`;
}

export async function deleteTaskAttachment(taskId: number, attachmentId: number): Promise<void> {
  const response = await fetch(`${API_URL}/api/tasks/${taskId}/attachments/${attachmentId}`, {
    method: "DELETE",
  });
  if (!response.ok) throw await responseError(response);
}

export async function deleteNewTask(taskId: number): Promise<void> {
  const response = await fetch(`${API_URL}/api/tasks/${taskId}`, { method: "DELETE" });
  if (!response.ok && response.status !== 404) throw await responseError(response);
}
