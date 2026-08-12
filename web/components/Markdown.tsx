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

function Markdown({ text }: { text: string }) {
  const lines = text.split("\n");
  const blocks: { type: string; content: string[] }[] = [];
  let current: { type: string; content: string[] } | null = null;

  const push = () => {
    if (current) blocks.push(current);
    current = null;
  };

  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) {
      push();
      continue;
    }
    const header = trimmed.match(/^#{1,4}\s+(.*)$/);
    const bullet = trimmed.match(/^[-*]\s+(.*)$/);
    const numbered = trimmed.match(/^\d+\.\s+(.*)$/);
    if (header) {
      push();
      blocks.push({ type: "h", content: [header[1]] });
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
            <div key={i} style={{ fontWeight: 700, marginTop: 10, fontSize: 13.5 }}>
              {renderInline(block.content[0])}
            </div>
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
