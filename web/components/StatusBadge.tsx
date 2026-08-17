"use client";

// Raw backend status value (all 14 TaskStatus/Execution states), used only
// on the legacy/advanced pages. Deliberately a neutral mono chip rather
// than a colorful pill — the colorful, authoritative vocabulary for status
// is `.stage-badge` (see lib/workStages.ts). Two colorful systems for
// "status" would compete; this one reads as a technical value instead.
export default function StatusBadge({ status }: { status: string }) {
  return <span className="status-chip">{status.replace(/_/g, " ")}</span>;
}
