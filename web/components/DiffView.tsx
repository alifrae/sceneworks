"use client";

import { useState } from "react";
import { Diff } from "@/lib/types";

function lineClass(line: string): string {
  if (line.startsWith("+++") || line.startsWith("---")) return "";
  if (line.startsWith("+")) return "add";
  if (line.startsWith("-")) return "del";
  if (line.startsWith("@@")) return "hunk";
  return "";
}

interface DiffFile {
  path: string;
  lines: string[];
  additions: number;
  deletions: number;
}

// The backend returns one unified diff blob (`diff.full`) with no per-file
// structure of its own — but unified diffs already delimit files with
// `diff --git a/<path> b/<path>` headers, so this is a straight parse of
// data that already exists, not a new backend contract.
function parseFiles(full: string): DiffFile[] {
  const lines = full.split("\n");
  const files: DiffFile[] = [];
  let current: DiffFile | null = null;

  for (const line of lines) {
    const header = line.match(/^diff --git a\/(.+) b\/(.+)$/);
    if (header) {
      if (current) files.push(current);
      current = { path: header[2] || header[1], lines: [], additions: 0, deletions: 0 };
      continue;
    }
    if (!current) continue;
    current.lines.push(line);
    if (line.startsWith("+") && !line.startsWith("+++")) current.additions++;
    if (line.startsWith("-") && !line.startsWith("---")) current.deletions++;
  }
  if (current) files.push(current);
  return files;
}

function FileDiff({ file, defaultOpen }: { file: DiffFile; defaultOpen: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="diff-file">
      <button className="diff-file-head" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <span className="diff-file-caret" aria-hidden>
          {open ? "▾" : "▸"}
        </span>
        <span className="diff-file-path" title={file.path}>
          {file.path}
        </span>
        <span className="diff-count add">+{file.additions}</span>
        <span className="diff-count del">-{file.deletions}</span>
      </button>
      {open && (
        <div className="diff">
          {file.lines.map((line, i) => (
            <pre key={i} className={lineClass(line)}>
              {line || " "}
            </pre>
          ))}
        </div>
      )}
    </div>
  );
}

export default function DiffView({ diff }: { diff: Diff | null }) {
  if (!diff) return <div className="empty">No diff yet.</div>;
  if (diff.error) return <div className="notice error">{diff.error}</div>;
  if (!diff.full && !diff.stat) return <div className="empty">No changes committed.</div>;

  const files = parseFiles(diff.full);

  if (files.length === 0) {
    // Unparseable/unexpected diff shape — fall back to the raw blob rather
    // than silently showing nothing.
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

  return (
    <div>
      <div className="diff-summary">
        <span className="diff-stat-pill">
          {files.length} file{files.length === 1 ? "" : "s"} changed
        </span>
        <span className="diff-count add">+{files.reduce((n, f) => n + f.additions, 0)}</span>
        <span className="diff-count del">-{files.reduce((n, f) => n + f.deletions, 0)}</span>
      </div>
      <div className="diff-files">
        {files.map((file, i) => (
          <FileDiff key={file.path} file={file} defaultOpen={files.length === 1 || i === 0} />
        ))}
      </div>
    </div>
  );
}
