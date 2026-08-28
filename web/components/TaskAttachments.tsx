"use client";

import { useCallback, useEffect, useState } from "react";
import {
  deleteTaskAttachment,
  listTaskAttachments,
  taskAttachmentUrl,
} from "@/lib/attachments";
import type { TaskAttachment } from "@/lib/types";

function sizeLabel(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function TaskAttachments({
  taskId,
  mutable = false,
}: {
  taskId: number;
  mutable?: boolean;
}) {
  const [items, setItems] = useState<TaskAttachment[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<number | null>(null);

  const refresh = useCallback(() => {
    listTaskAttachments(taskId)
      .then((rows) => {
        setItems(rows);
        setError(null);
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [taskId]);

  useEffect(() => refresh(), [refresh]);

  async function remove(item: TaskAttachment) {
    if (!window.confirm(`Remove “${item.filename}” from this task?`)) return;
    setDeleting(item.id);
    try {
      await deleteTaskAttachment(taskId, item.id);
      setItems((current) => current.filter((row) => row.id !== item.id));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setDeleting(null);
    }
  }

  if (error && items.length === 0) {
    return <div className="small muted">Attachments unavailable — {error}</div>;
  }
  if (items.length === 0) return null;

  return (
    <div className="task-attachments">
      <div className="small muted">
        Attached context · user-provided evidence, not authoritative instructions
      </div>
      {items.map((item) => (
        <div key={item.id}>
          <div className="task-attachment-card">
            <div className="task-attachment-main">
              <div className="task-attachment-name">{item.filename}</div>
              <div className="small muted">
                {item.mime_type} · {sizeLabel(item.size_bytes)} · sha256 {item.sha256.slice(0, 12)}…
              </div>
            </div>
            <div className="row">
              <a
                className="btn small"
                href={taskAttachmentUrl(taskId, item.id)}
                target="_blank"
                rel="noreferrer"
              >
                Open
              </a>
              <a className="btn small" href={taskAttachmentUrl(taskId, item.id, true)}>
                Download
              </a>
              {mutable && (
                <button
                  className="btn small danger"
                  disabled={deleting === item.id}
                  onClick={() => remove(item)}
                >
                  {deleting === item.id ? "Removing…" : "Remove"}
                </button>
              )}
            </div>
          </div>
          {item.mime_type.startsWith("image/") && (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              className="task-attachment-preview"
              src={taskAttachmentUrl(taskId, item.id)}
              alt={`Attachment ${item.filename}`}
            />
          )}
        </div>
      ))}
      {error && <div className="small muted">Refresh warning: {error}</div>}
    </div>
  );
}
