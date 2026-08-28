# Repository Agent Rules

These rules apply to every coding agent working in SceneWorks.

## Operating principles

- Inspect the existing implementation, tests, and canonical documentation before modifying anything.
- Prefer the smallest compatible change that satisfies the task.
- Reuse existing patterns and abstractions before adding new ones.
- Avoid unrelated refactors, speculative helpers, and broad cleanup.
- Do not guess material requirements or silently fix unrelated issues; report them instead.
- Use targeted tests during development and broader regression/qualification only at final verification.
- Avoid destructive Git/filesystem operations. Preserve human work and agent worktrees.
- Stop once the acceptance criteria are met.
- Report changes made, verification performed, and remaining risks.

## SceneWorks architecture invariants

1. **Model provider, agent backend, and execution runtime are separate concepts.**
   - A model generates reasoning/text.
   - An `AgentBackend` is an autonomous worker transport/runtime.
   - An `ExecutionRuntime` exposes SceneWorks-owned machine capabilities.
   Do not collapse these layers.

2. **Gemini CLI is the default, not a platform dependency.**
   Gemini ACP may provide richer provider-native capabilities, but SceneWorks core project/task/runtime/MCP semantics must remain usable when Gemini is unavailable or unauthenticated.

3. **ACP is one adapter transport, not SceneWorks' agent protocol.**
   New workers may use ACP, HTTP, SDKs, CLI/headless mode, or another bounded adapter. Protocol-specific types must not leak beyond the backend adapter.

4. **Direct MCP engineering control is SceneWorks-owned.**
   Workspace, command/process and Git MCP tools operate only through an `EngineeringSession` and an `ExecutionRuntime`; they must never call a provider model to perform the primitive operation.

5. **Worktree and permission gates are mandatory.**
   Direct filesystem tools reject absolute paths and path/symlink escapes. MCP responses must not expose host worktree paths. Process operations must verify session ownership. New tools must declare and enforce the minimum required EngineeringSession permission.

6. **Shell/process execution is not an OS sandbox.**
   Worktree cwd/path confinement does not prevent a subprocess from exercising the SceneWorks user's OS authority or network access. Do not describe it as sandboxed unless an actual OS/container boundary is implemented.

7. **No silent provider fallback after mutation.**
   A clean, not-yet-started execution may be rerouted deliberately to a healthy backup. If a worker has already mutated a worktree, preserve that work and surface the failure; do not silently hand the same worktree to another autonomous agent.

8. **Gemini-native capabilities stay provider-native.**
   Do not clone Gemini web search/fetch, native subagents, or provider-specific reasoning features into the native execution runtime merely for parity.

9. **SceneWorks-captured state is evidence; agent conclusions are inference.**
   Git state, file hashes, command/process results, runtime observations and persisted SceneWorks events are authoritative observations of what occurred. Gemini/OpenCode/OpenHands summaries must never silently replace or override them.

10. **Engineering evidence must be causally attributable.**
    Direct Advanced actions should be attributable to `EngineeringSession -> Task (when bound) -> EngineeringTurn -> action_id`. Delegated executions must retain the originating turn/action identifiers. Do not add new Advanced control paths that bypass this correlation model.

11. **The evidence ledger is not a repository mirror.**
    File evidence records paths, ranges and hashes; writes record before/after hashes. Git evidence may retain status/stat/hash metadata while the real diff remains Git truth. Persist full source/diff contents only when an explicit artifact requirement justifies it. Command/process output must be bounded.

12. **Git provenance remains SceneWorks authority.**
    Worktrees, base commits, diffs, changed-file hashes, branch names and persisted execution/session records are owned by SceneWorks. Agent prose is not evidence of repository state.

13. **Settings changes must reach live consumers.**
    When rebuilding a backend registry, router, Git service or runtime dependency, update every long-lived service that holds that dependency. A setting that appears saved but requires an undocumented restart is a defect.

14. **PCS lifecycle control is semantic and SceneWorks-owned.**
    `pcs.start/stop/restart/status`, log capture, health checks and verification runbooks must use SceneWorks runtime primitives directly. Do not route those operations through Gemini/OpenCode/OpenHands simply to gain terminal access.

15. **Prefer deterministic PCS APIs over GUI automation.**
    If PCS exposes runtime state or a deterministic operation through its hardened API, use that source. GUI screenshots/automation are fallback observation/control mechanisms and must not become the primary API for behavior that PCS can expose semantically.

16. **PCS runtime observations are evidence.**
    Managed-process state, stdout/stderr, configured health probes, crash/exit state, runtime API responses and verification-runbook results belong in SceneWorks evidence. Agent interpretations of those observations remain inference.

17. **External PCS assets require explicit read-only aliases.**
    Recordings/corpora outside the worktree are available only through project-scoped configured roots plus `external_asset_read`. MCP must expose the alias and relative metadata, never the configured absolute root. WP16 external roots are read-only; do not widen them into general filesystem access.

18. **PCS configuration is not a secret store or network scanner.**
    Persisted run profiles/runbooks must not contain credentials or secret-bearing environment values. Health/runtime API probes are loopback-only unless a future isolated/network-governance design explicitly widens the boundary.

19. **Managed PCS processes must not be orphaned.**
    An EngineeringSession/project cannot be closed/deleted while it still owns a live managed PCS run. SceneWorks restart recovery must mark unrecoverable native handles LOST and preserve that fact as evidence.

## Backend additions

- Implement `AgentBackend.run()`, `cancel()`, and `health()` in `backend/app/agents/<backend>.py`.
- Keep provider credentials outside persisted SceneWorks settings unless an explicit secure-secret design exists.
- Emit only generic SceneWorks event vocabulary.
- State the backend's actual permission/confinement strength; do not claim parity with Gemini ACP unless verified.
- Add deterministic tests that do not require a paid/live provider.
- Update `docs/backends.md` and the Settings UI when the backend is operator-selectable.

## Runtime additions

- Implement the `ExecutionRuntime` contract under `backend/app/runtime/`.
- Runtime methods contain no model reasoning or prompt logic.
- Treat every path from MCP as untrusted.
- Keep command execution argument-based; do not introduce shell-string evaluation without an explicit requirement and security review.
- Persistent process output must be bounded and attributable to its EngineeringSession.
- When a runtime primitive becomes part of Advanced control, add or update its evidence mapping in the same change.

## PCS control additions

- Keep PCS-specific profiles/runbooks/assets under the PCS control domain rather than adding product-specific fields to the generic `Project` model.
- Keep launch working directories and configured log/crash paths worktree-relative.
- Treat `{{asset:<alias>:<relative-path>}}` as a governed server-side resolution mechanism; never persist or echo the resolved absolute path to MCP evidence/results.
- Record non-zero unmanaged exits as crash evidence and preserve bounded final stdout/stderr before finalizing the run.
- Verification runbooks are deterministic procedures, not prompts. Every step must have objective pass/fail output and evidence correlation.
- Do not delete configured external asset files during project/session cleanup.

## Documentation policy

- Update an existing canonical document whenever possible.
- Keep architecture, backend status, limitations, MCP tutorial, and `.env.example` consistent with behavior.
- Do not create ad-hoc investigation Markdown in source directories.
- Code/API behavior changes must update the corresponding canonical documentation in the same change.
- Temporary notes and generated diagnostics are not documentation and should not be committed unless explicitly required.
