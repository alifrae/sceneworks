"use client";

import { memo, useEffect, useMemo, useState } from "react";
import { AppEvent } from "@/lib/types";
import { api, eventsUrl } from "@/lib/api";
import { eventLabel, formatTime } from "@/lib/format";

interface EventLogProps {
  taskId?: number;
  executionId?: string;
  compact?: boolean;
  onEvent?: (event: AppEvent) => void;
}

function renderBody(event: AppEvent): string {
  const p = event.payload;
  if (event.type === "task.transitioned") {
    return `${p.from} → ${p.to} (${p.action}, by ${p.actor})`;
  }
  if (event.type === "file.changed") return String(p.path ?? "");
  if (event.type === "command.started") return `$ ${p.command}`;
  if (event.type === "command.output") return String(p.output ?? "");
  if (event.type === "tool.started") return `${p.tool ?? "tool"}`;
  if (event.type === "agent.message") return String(p.text ?? "");
  if (event.type === "agent.text_delta") return String(p.delta ?? "");
  if (event.type === "agent.thought_summary") return String(p.text ?? "");
  if (event.type === "git.commit") return `${p.commit}`;
  if (event.type === "task.note") return `${p.title}${p.detail ? `: ${p.detail}` : ""}`;
  if (event.type === "execution.failed" || event.type === "execution.interrupted")
    return String(p.error ?? "");
  if (event.type === "execution.completed") return String(p.summary ?? "");
  if (event.type === "artifact.created") return `artifact #${p.artifact_id} (${p.role})`;
  if (event.type === "agent.event") {
    const text =
      (p.text as string) ||
      (p.message as string) ||
      (p.error as string) ||
      (p.name as string) ||
      JSON.stringify(p);
    return text;
  }
  if (Object.keys(p).length === 0) return "";
  return JSON.stringify(p);
}

export default function EventLog({ taskId, executionId, compact, onEvent }: EventLogProps) {
  const [events, setEvents] = useState<AppEvent[]>([]);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    setEvents([]);
    let closed = false;
    let source: EventSource | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let retry = 0;

    async function loadReplay() {
      try {
        const rows = taskId !== undefined
          ? await api.taskEvents(taskId)
          : executionId
            ? await api.executionEvents(executionId)
            : [];
        if (!closed) {
          setEvents((previous) => {
            const merged = new Map(previous.map((event) => [event.id, event]));
            for (const event of rows) merged.set(event.id, event);
            return Array.from(merged.values()).sort((a, b) => a.id - b.id).slice(-800);
          });
        }
      } catch {
        /* backend may be down; SSE will retry */
      }
    }

    function connect() {
      source = new EventSource(eventsUrl(taskId, executionId));
      source.onopen = () => {
        retry = 0;
        setConnected(true);
      };
      source.onmessage = (msg) => {
        const event = JSON.parse(msg.data) as AppEvent;
        if (event.type === "heartbeat") return;
        setEvents((prev) => {
          if (prev.some((e) => e.id === event.id)) return prev;
          return [...prev, event].slice(-800);
        });
        onEvent?.(event);
        setConnected(true);
      };
      source.onerror = () => {
        setConnected(false);
        source?.close();
        if (!closed) {
          // Reconnect with exponential backoff (2s → 30s cap) instead of a
          // fixed 2s loop, so a downed API cannot turn the page into a
          // constant stream of failing connections.
          const delay = Math.min(2000 * 2 ** retry, 30_000);
          retry += 1;
          timer = setTimeout(connect, delay);
        }
      };
    }

    loadReplay();
    connect();
    return () => {
      closed = true;
      if (timer) clearTimeout(timer);
      source?.close();
    };
  }, [taskId, executionId, onEvent]);

  const visible = useMemo(() => compact ? events.slice(-120) : events, [compact, events]);

  return (
    <div>
      <div className="row space-between" style={{ marginBottom: 8 }}>
        <h3>Activity / Logs</h3>
        <span className={`small muted ${connected ? "" : ""}`}>
          {connected ? "● live" : "○ connecting"}
        </span>
      </div>
      {visible.length === 0 ? (
        <div className="empty">No events yet.</div>
      ) : (
        <div className="log">
          {visible.map((event) => <EventEntry key={event.id} event={event} />)}
        </div>
      )}
    </div>
  );
}

const EventEntry = memo(function EventEntry({ event }: { event: AppEvent }) {
  return (
    <div
      className={`entry ${event.severity === "error" ? "error" : ""} ${
        event.severity === "warning" ? "warning" : ""
      } ${event.payload.diagnostics ? "diag" : ""}`}
    >
      <span className="time">{formatTime(event.timestamp).slice(11, 19)}</span>
      <span className="type">{eventLabel(event.type)}</span>
      <span className="body">{renderBody(event) || "—"}</span>
    </div>
  );
});
