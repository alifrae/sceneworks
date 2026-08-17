"use client";

// One tasks snapshot for the whole app, one network request per poll tick.
//
// Before this module, the Sidebar and the Home page each ran their own
// interval against /api/tasks with different query strings (`limit=6` and
// `limit=50`), so every mounted page made duplicate list requests that the
// per-key request cache could not coalesce. All consumers now read the same
// snapshot, refreshed by a single visibility-aware poller that only runs
// while someone is subscribed.

import { useEffect, useState } from "react";
import { api, errorMessage } from "@/lib/api";
import type { Task } from "@/lib/types";

export type TasksSnapshot = {
  tasks: Task[] | null;
  error: string | null;
};

const POLL_MS = 8_000;

const snapshot: TasksSnapshot = { tasks: null, error: null };
const listeners = new Set<(state: TasksSnapshot) => void>();
let timer: ReturnType<typeof setInterval> | null = null;
let started = false;

function emit(): void {
  // A fresh object per emit so setState never bails out on referential
  // equality while the underlying snapshot is mutated in place.
  const current: TasksSnapshot = { tasks: snapshot.tasks, error: snapshot.error };
  for (const listener of listeners) listener(current);
}

async function poll(): Promise<void> {
  try {
    snapshot.tasks = await api.tasks({ limit: "200" });
    snapshot.error = null;
  } catch (e) {
    // Keep the last good list; surface a failure only while there is no
    // data at all, otherwise a transient error would blank a populated
    // page for a whole poll interval.
    if (snapshot.tasks === null) snapshot.error = errorMessage(e);
  }
  emit();
}

function onVisibilityChange(): void {
  if (document.visibilityState === "visible") void poll();
}

function start(): void {
  if (started) return;
  started = true;
  void poll();
  timer = setInterval(() => {
    if (document.visibilityState === "visible") void poll();
  }, POLL_MS);
  document.addEventListener("visibilitychange", onVisibilityChange);
}

function stop(): void {
  if (!started) return;
  started = false;
  if (timer !== null) {
    clearInterval(timer);
    timer = null;
  }
  document.removeEventListener("visibilitychange", onVisibilityChange);
}

export function useTasks(): TasksSnapshot {
  const [state, setState] = useState<TasksSnapshot>(snapshot);

  useEffect(() => {
    listeners.add(setState);
    start();
    return () => {
      listeners.delete(setState);
      if (listeners.size === 0) stop();
    };
  }, []);

  return state;
}
