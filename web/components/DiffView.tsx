"use client";

import { Diff } from "@/lib/types";

function lineClass(line: string): string {
  if (line.startsWith("+++") || line.startsWith("---")) return "";
  if (line.startsWith("+")) return "add";
  if (line.startsWith("-")) return "del";
  if (line.startsWith("@@")) return "hunk";
  return "";
}

export default function DiffView({ diff }: { diff: Diff | null }) {
  if (!diff) return <div className="empty">No diff yet.</div>;
  if (diff.error) return <div className="notice error">{diff.error}</div>;
  if (!diff.full && !diff.stat) return <div className="empty">No changes committed.</div>;
  const lines = diff.full.split("\n");
  return (
    <div>
      <pre style={{ color: "#9aa4b2", marginBottom: 8 }}>{diff.stat || "(no stat)"}</pre>
      <div className="diff">
        {lines.map((line, i) => (
          <pre key={i} className={lineClass(line)}>
            {line || " "}
          </pre>
        ))}
      </div>
    </div>
  );
}
