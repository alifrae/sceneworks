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

15. **Prefer deterministic PCS APIs over GUI automation or visual inference.**
    If PCS exposes runtime state or a deterministic operation through its hardened API, use that source. GUI screenshots/automation are fallback observation/control mechanisms and must not become the primary API for behavior that PCS can expose semantically.

16. **PCS runtime observations are evidence.**
    Managed-process state, stdout/stderr, configured health probes, crash/exit state, runtime API responses and verification-runbook results belong in SceneWorks evidence. Agent interpretations of those observations remain inference.

17. **External PCS assets require explicit read-only aliases.**
    Recordings/corpora outside the worktree are available only through project-scoped configured roots plus `external_asset_read`. MCP must expose the alias and relative metadata, never the configured absolute root. WP16 external roots are read-only; do not widen them into general filesystem access.

18. **PCS configuration is not a secret store or network scanner.**
    Persisted run profiles/runbooks must not contain credentials or secret-bearing environment values. Health/runtime API probes are loopback-only unless a future isolated/network-governance design explicitly widens the boundary.

19. **Managed PCS processes must not be orphaned.**
    An EngineeringSession/project cannot be closed/deleted while it still owns a live managed PCS run. SceneWorks restart recovery must mark unrecoverable native handles LOST and preserve that fact as evidence.

20. **GUI observation is scoped to the SceneWorks-managed PCS process.**
    `gui_observe` may enumerate/capture only windows belonging to the current managed PCS PID. MCP must not accept an arbitrary PID or expose a generic desktop/window screenshot primitive. Opaque window identifiers are valid only inside that managed-PID relationship.

21. **Screenshot bytes are artifacts; visual interpretation is inference.**
    Persist screenshot/diff bytes outside Git worktrees and record hashes, dimensions, capture semantics and causal references in the evidence ledger. Do not duplicate image bytes in evidence JSON or expose internal storage paths. Pixel/hash comparisons are evidence; semantic claims about what an image means are interpretation.

22. **WP17 observation remains a separate, read-only capability boundary.**
    `gui_observe` alone never grants UI mutation. Window/dialog discovery, screenshots and deterministic visual comparison remain usable without `gui_automate`. Do not silently make observation permission imply automation.

23. **WP18 GUI mutation requires an explicit automation permission.**
    `gui_automate` is independent from `gui_observe`, and WP18 mutation requires both. Automation targets must be opaque accessibility control ids discovered under a current visible SceneWorks-managed PCS window. Do not accept arbitrary PIDs, HWNDs, desktop coordinates, pointer movement or keyboard injection as WP18 MCP inputs.

24. **GUI actions are evidence-first and must fail closed when verification is incomplete.**
    Capture durable pre-action visual evidence before mutation. After mutation, capture durable post-action evidence and run deterministic visual comparison. If the action may have executed but after-action evidence or comparison fails, record the action as partial/unverified and surface an error; never report it as successfully verified.

25. **GUI text/value evidence must not become a secret echo channel.**
    Do not persist user-provided values typed through GUI automation. Record bounded metadata such as character count and SHA-256 when correlation is needed. Accessibility names/automation ids may be evidence; control values are not automatically evidence.

26. **SceneWorks infrastructure lifecycle is semantic and supervisor-owned.**
    API, web, and MCP-tunnel start/stop/restart/recovery belong to the out-of-process loopback lifecycle supervisor. FastAPI, provider agents, browser JavaScript, and the Windows launcher are clients, not lifecycle authorities. Mutating interfaces accept semantic component/action enums only; they must not expose arbitrary PID, port, executable, path, URL, environment, command, or shell input. A port match alone never authorizes process termination; ambiguous process ownership must fail closed. Automatic recovery must remain bounded and journaled before process mutation.

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

## GUI evidence additions

- Put platform-specific window/capture code behind the GUI observation provider boundary; keep MCP/session/evidence semantics platform-neutral.
- Fresh observation must derive the PID from the EngineeringSession's live managed `PcsRun`; never accept arbitrary PID input from MCP.
- Record whether capture is occlusion-safe. Do not claim independent window rendering when the implementation captures a visible screen region.
- Persist GUI artifacts under SceneWorks-owned project storage so normal project purge removes them, but never place them in Git worktrees.
- Stored screenshots/diffs must remain retrievable and comparable after the observed PCS run ends.
- Add deterministic provider fakes for CI; do not make the normal regression suite depend on a desktop session or live PCS installation.

## GUI automation additions

- Put platform-specific accessibility automation behind `GuiAutomationProvider`; MCP/session/evidence policy remains provider-neutral.
- Prefer accessibility/UI Automation patterns (`Invoke`, `Value`, `SelectionItem`, `Toggle`) over simulated pointer/keyboard input.
- Bind every control id to one managed PCS window and re-resolve it under that window at action time. Stale or forged ids must fail rather than retargeting another application.
- The Windows provider may use a fixed internal PowerShell/.NET UI Automation adapter, but caller-controlled script text must never cross that boundary.
- Before/after screenshots and visual comparison are part of the action contract, not optional diagnostics.
- Add deterministic fake automation providers for CI; Windows/UIA live qualification is a separate host-level validation and must not be fabricated on Linux CI.

## Lifecycle supervisor additions

- Keep the supervisor standard-library core independent from FastAPI and model/provider adapters.
- Persist only bounded ownership metadata and operation state; never persist environment dictionaries or lifecycle credentials in the operation journal/process metadata.
- Keep the HTTP control service loopback-only. Widening it to remote access requires a separate trust/authentication design and is not an incremental WP21 change.
- Any new lifecycle target must have a fixed semantic component identity, explicit health contract, startup grace, ownership proof, bounded recovery policy, and deterministic tests before it can be exposed to UI/MCP.
- Real Windows process/tunnel failure injection is host qualification; do not fabricate it from Linux or GitHub-hosted evidence.

## Documentation policy

- Update an existing canonical document whenever possible.
- Keep architecture, backend status, limitations, MCP tutorial, and `.env.example` consistent with behavior.
- Do not create ad-hoc investigation Markdown in source directories.
- Code/API behavior changes must update the corresponding canonical documentation in the same change.
- Temporary notes and generated diagnostics are not documentation and should not be committed unless explicitly required.
