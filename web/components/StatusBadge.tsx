"use client";

import { STATUS_COLORS } from "@/lib/format";

export default function StatusBadge({ status }: { status: string }) {
  return (
    <span className="badge" style={{ background: STATUS_COLORS[status] ?? "#64748b" }}>
      {status.replace(/_/g, " ")}
    </span>
  );
}
