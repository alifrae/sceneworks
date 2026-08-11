# Gemini ACP Capability Matrix

SceneWorks V2.5 safety audit of 191 Gemini ACP operations observed during
real PCS usage. 28 reached the SceneWorks permission gate; 163 were reported
as after-the-fact notifications. This document classifies every capability
category and states how it is mediated.

## Capability categories

| # | Capability | Example | Read files? | Mutate files? | Execute processes? | Alter Git? | Access network? | Mediated? | Policy |
|---|---|---|---|---|---|---|---|---|---|
| 1 | `fs/read_text_file` | Agent reads a source file | Yes | No | No | No | No | **Yes** — confined to worktree | `fs/read` always allowed (cwd-gated); reads outside worktree denied |
| 2 | `fs/write_text_file` | Agent edits a file | No | Yes | No | No | No | **Yes** — require `allow_write` + worktree confinement | `repository_write` permission required |
| 3 | `session/request_permission` (non-execute) | Agent wants to read/search/fetch within its runtime | — | — | No | No | No | **Yes** — allow/deny selected from agent's options | Read/search/fetch/think kinds always allowed |
| 4 | `session/request_permission` (execute) | Agent wants to run a shell command | — | — | Yes | Possible | Possible | **Yes** — `shell_execute` required | Refused for read-only roles (Architect, CEO, CTO, Product, GTM) |
| 5 | `terminal/create` | Agent spawns a subprocess | — | — | Yes | Possible | Possible | **Yes** — `shell_execute` + cwd validation | shell/write roles only; cwd confined to worktree |
| 6 | `terminal/output` | Agent reads command output | No | No | No | No | No | **Yes** | Always permitted for existing terminal |
| 7 | `terminal/wait_for_exit` | Agent waits for command | No | No | No | No | No | **Yes** | Always permitted for existing terminal |
| 8 | `terminal/kill` / `terminal/release` | Agent terminates command | No | No | No | No | No | **Yes** | Always permitted for existing terminal |
| 9 | Gemini internal `list_directory` | Agent lists files (built-in tool) | Yes | No | No | No | No | **No** — notification only | Relies on Gemini's own cwd confinement; cannot be mediated by SceneWorks |
| 10 | Gemini internal `search_file` / `grep` | Agent searches codebase (built-in tool) | Yes | No | No | No | No | **No** — notification only | Relies on Gemini's own cwd confinement |
| 11 | Gemini internal `read_file` (not ACP) | Agent reads file via built-in tool | Yes | No | No | No | No | **No** — notification only | May use ACP `fs/read_text_file` in newer versions; otherwise confined by cwd |
| 12 | Gemini internal `web_fetch` | Agent fetches a URL | No | No | No | No | Yes | **No** — notification only | Network access is **unmediated**; documented limitation |
| 13 | Gemini internal `web_search` | Agent searches the web | No | No | No | No | Yes | **No** — notification only | Network access is **unmediated**; documented limitation |
| 14 | Gemini internal `run_shell_command` (not ACP) | Agent runs shell via built-in tool | — | — | Yes | Possible | Possible | **No** — notification only | May use ACP `terminal/create` in newer versions; otherwise cwd-gated only |
| 15 | `session/update` (agent_message_chunk) | Streaming assistant response | No | No | No | No | No | **Status only** | Harmless — text output, no side effects |
| 16 | `session/update` (agent_thought_chunk) | Streaming reasoning | No | No | No | No | No | **Status only** | Harmless — reasoning, no side effects |
| 17 | `session/update` (tool_call) | Tool start notification | No | No | No | No | No | **Status only** | Informational — already-executed or in-progress |
| 18 | `session/update` (tool_call_update) | Tool completion notification | No | No | No | No | No | **Status only** | Informational — after-the-fact report |
| 19 | `session/update` (plan) | Agent plan notification | No | No | No | No | No | **Status only** | Harmless — reasoning, no side effects |
| 20 | `notifications/cancelled` | Agent cancelled a request | No | No | No | No | No | **Status only** | Harmless |
| 21 | `notifications/progress` | Agent progress update | No | No | No | No | No | **Status only** | Harmless |
| 22 | `notifications/initialized` | Agent initialization complete | No | No | No | No | No | **Status only** | Harmless |
| 23 | Unknown notification | New/unrecognized capability | Unknown | Unknown | Unknown | Unknown | Unknown | **Fail closed** | Diagnostic emitted; no side effects blocked but flagged |

## Mediation boundary

```
┌─────────────────────────────────────────────────────────────┐
│ SceneWorks mediation boundary                                │
│                                                              │
│  ✅ fs/read_text_file    → worktree confined                 │
│  ✅ fs/write_text_file   → allow_write + worktree confined   │
│  ✅ request_permission   → allow_shell for execute kind       │
│  ✅ terminal/create      → allow_shell + cwd validated        │
│  ✅ terminal/*           → existing terminal only             │
│                                                              │
│  ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ │
│                                                              │
│  ⚠️ Gemini internal tools (list_directory, search_file,      │
│     read_file, web_fetch, web_search, run_shell_command)     │
│     → notified after the fact; cwd-gated but unmediated      │
│                                                              │
│  ⚠️ Network access (web_fetch, web_search)                   │
│     → completely unmediated                                  │
│                                                              │
│  ❌ Unknown capability types                                 │
│     → fail closed (error response to agent)                  │
└─────────────────────────────────────────────────────────────┘
```

## What stays outside SceneWorks control

1. **Gemini built-in tool operations** — Gemini's own `list_directory`, `search_file`,
   `web_fetch`, `web_search`, and internal `run_shell_command` run inside the Gemini
   process and are reported as `session/update` notifications only. SceneWorks
   cannot intercept, approve, or deny them. They are confined by Gemini's cwd
   (which SceneWorks sets to the worktree) but not by SceneWorks permission checks.

2. **Network access** — The `NETWORK_ACCESS` permission is declarative only. Neither
   the ACP proxy nor any other SceneWorks component intercepts network traffic.
   Gemini can make HTTP requests through its built-in `web_fetch` and `web_search`
   tools regardless of role permissions.

3. **Shell command reach** — `SHELL_EXECUTE` permits a subprocess, but the
   subprocess runs with the SceneWorks user's OS privileges. It can reach
   anything the OS allows. SceneWorks validates the cwd but does not sandbox
   the child process.

4. **New/unknown ACP methods** — Any ACP client method or notification not in the
   known set is logged as a diagnostic event and its client request receives an
   error response. The agent may still have executed some operation internally.

## Safe by design

- **Worktree confinement**: Gemini's cwd is set to a commit-pinned detached worktree.
  Path traversal (`../../`) resolves within the worktree; absolute paths outside it
  are rejected by the ACP file proxy.
- **Permission gating**: `repository_write` and `shell_execute` are checked before
  any write or shell operation reaches the OS.
- **Audit trail**: Every mediated operation (and every denial) emits a structured
  event visible in the UI event log.
- **No automatic merge**: SceneWorks never merges agent branches into human branches.
- **Isolation**: The human working tree is never the agent's cwd; agents never
  read or write it.

## Known gaps (V2.5 accepted)

- Gemini internal tools (9-14 above) are not mediated — these are capabilities of
  the Gemini runtime, not the ACP protocol. Full mediation would require an
  ACP protocol extension or a container/VM-level sandbox.
- Network access is entirely unmediated.
- The shell sandbox is a cwd gate, not a container/jail.
- No authentication on the API.
- Single-machine, single-user deployment.

See [docs/limitations.md](limitations.md) for the full list.
