"""Formatting helpers shared by workflow runners (WP7)."""

from __future__ import annotations


def format_memory_block(memories: list[dict]) -> str:
    """Render accepted project memories as agent-usable authoritative prose."""
    lines = [
        "## Accepted project decisions and constraints",
        "",
        "These were accepted by the human founder and are authoritative. "
        "Follow them. If one appears wrong for this task, say so rather than "
        "silently departing from it.",
        "",
    ]
    for item in memories:
        lines.append(f"### [{item['type']}] {item['title']}")
        provenance = [f"source: {item.get('source') or 'unknown'}"]
        if item.get("source_task_id"):
            provenance.append(f"from task {item['source_task_id']}")
        if item.get("created_at"):
            provenance.append(f"recorded {item['created_at']}")
        lines.append(f"_({'; '.join(provenance)})_")
        if item.get("tags"):
            lines.append(f"_tags: {', '.join(item['tags'])}_")
        lines.append("")
        lines.append(item.get("content") or "")
        lines.append("")
    return "\n".join(lines).rstrip()


def cap_text(text: str, max_bytes: int) -> str:
    if len(text.encode("utf-8")) <= max_bytes:
        return text
    return (
        text.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")
        + "\n[truncated]"
    )
