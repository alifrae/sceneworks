// Small formatting helpers.

import { EVENT_LABELS } from "./eventLabels";

export function formatTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString();
}

export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "—";
  const diff = Date.now() - new Date(iso).getTime();
  if (diff < 60_000) return "just now";
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
  return `${Math.floor(diff / 86_400_000)}d ago`;
}

export function eventLabel(type: string): string {
  return EVENT_LABELS[type] ?? type.replace(/\./g, " ");
}

export function shortId(id: string): string {
  return id.length > 10 ? id.slice(0, 8) : id;
}

export const STATUS_COLORS: Record<string, string> = {
  NEW: "#64748b",
  ARCHITECTURE_ANALYSIS: "#3b82f6",
  AWAITING_ARCHITECTURE_APPROVAL: "#f59e0b",
  READY_TO_IMPLEMENT: "#0ea5e9",
  IMPLEMENTING: "#3b82f6",
  TESTING: "#8b5cf6",
  REVIEWING: "#8b5cf6",
  CHANGES_REQUESTED: "#f97316",
  READY_FOR_HUMAN: "#f59e0b",
  ACCEPTED: "#22c55e",
  REJECTED: "#ef4444",
  FAILED: "#ef4444",
  CANCELLED: "#94a3b8",
  QUEUED: "#94a3b8",
  STARTING: "#3b82f6",
  RUNNING: "#3b82f6",
  COMPLETED: "#22c55e",
  INTERRUPTED: "#ef4444",
};
