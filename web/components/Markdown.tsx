"use client";

import { Fragment, memo } from "react";

// Tiny markdown renderer for agent outputs (headers, bullets, bold, code).
// Deliberately minimal — agent output is already structured text.

function renderInline(text: string): React.ReactNode[] {
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g);
  return parts.map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**"))
      return <strong key={i}>{part.slice(2, -2)}</strong>;
    if (part.startsWith("`") && part.endsWith("`"))
      return <code key={i}>{part.slice(1, -1)}</code>;
    return <Fragment key={i}>{part}</Fragment>;
  });
}

const HEADING_SIZE: Record<number, number> = { 1: 16, 2: 15, 3: 13.5, 4: 13 };

type Block =
  | { type: "h"; level: number; content: string[] }
  | { type: "ul" | "ol" | "p"; content: string[] }
  | { type: "code"; lang: string; content: string[] };

function Markdown({ text }: { text: string }) {
  const lines = text.split("\n");
  const blocks: Block[] = [];
  let current: Block | null = null;

  const push = () => {
    if (current) blocks.push(current);
    current = null;
  };

  for (let idx = 0; idx < lines.length; idx++) {
    const line = lines[idx];
    const trimmed = line.trim();

    const fence = trimmed.match(/^```\s*(\S*)$/);
    if (fence) {
      push();
      const lang = fence[1] || "";
      const codeLines: string[] = [];
      idx++;
      while (idx < lines.length && !/^```\s*$/.test(lines[idx].trim())) {
        codeLines.push(lines[idx]);
        idx++;
      }
      blocks.push({ type: "code", lang, content: codeLines });
      continue;
    }

    if (!trimmed) {
      push();
      continue;
    }
    const header = trimmed.match(/^(#{1,4})\s+(.*)$/);
    const bullet = trimmed.match(/^[-*]\s+(.*)$/);
    const numbered = trimmed.match(/^\d+\.\s+(.*)$/);
    if (header) {
      push();
      blocks.push({ type: "h", level: header[1].length, content: [header[2]] });
    } else if (bullet) {
      if (!current || current.type !== "ul") {
        push();
        current = { type: "ul", content: [] };
      }
      current.content.push(bullet[1]);
    } else if (numbered) {
      if (!current || current.type !== "ol") {
        push();
        current = { type: "ol", content: [] };
      }
      current.content.push(numbered[1]);
    } else {
      if (!current || current.type !== "p") {
        push();
        current = { type: "p", content: [] };
      }
      current.content.push(line);
    }
  }
  push();

  return (
    <div>
      {blocks.map((block, i) => {
        if (block.type === "h")
          return (
            <div
              key={i}
              style={{
                fontWeight: 650,
                marginTop: i === 0 ? 0 : 14,
                marginBottom: 4,
                fontSize: HEADING_SIZE[block.level] ?? 13,
              }}
            >
              {renderInline(block.content[0])}
            </div>
          );
        if (block.type === "code")
          return (
            <pre key={i}>
              <code>{block.content.join("\n") || " "}</code>
            </pre>
          );
        if (block.type === "ul")
          return (
            <ul key={i} style={{ margin: "6px 0", paddingLeft: 20 }}>
              {block.content.map((item, j) => (
                <li key={j}>{renderInline(item)}</li>
              ))}
            </ul>
          );
        if (block.type === "ol")
          return (
            <ol key={i} style={{ margin: "6px 0", paddingLeft: 20 }}>
              {block.content.map((item, j) => (
                <li key={j}>{renderInline(item)}</li>
              ))}
            </ol>
          );
        return (
          <p key={i} style={{ margin: "6px 0", whiteSpace: "pre-wrap" }}>
            {renderInline(block.content.join("\n"))}
          </p>
        );
      })}
    </div>
  );
}

export default memo(Markdown);
