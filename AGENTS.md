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
   - an `ExecutionRuntime` exposes SceneWorks-owned machine capabilities.
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

9. **Git provenance remains SceneWorks authority.**
   Worktrees, base commits, diffs, branch names and persisted execution/session records are owned by SceneWorks. Agent prose is not evidence of repository state.

10. **Settings changes must reach live consumers.**
    When rebuilding a backend registry, router, Git service or runtime dependency, update every long-lived service that holds that dependency. A setting that appears saved but requires an undocumented restart is a defect.

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

## Documentation policy

- Update an existing canonical document whenever possible.
- Keep architecture, backend status, limitations, MCP tutorial, and `.env.example` consistent with behavior.
- Do not create ad-hoc investigation Markdown in source directories.
- Code/API behavior changes must update the corresponding canonical documentation in the same change.
- Temporary notes and generated diagnostics are not documentation and should not be committed unless explicitly required.
