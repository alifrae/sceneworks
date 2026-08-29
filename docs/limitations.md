# Known Limitations

This file describes the **current** SceneWorks boundary after WP21. Historical V2/V3/WP documents should not be used as the current limitations list.

## Deployment and trust

- **Single-machine control plane.** FastAPI, workflow execution, SQLite, local runtimes and agent processes run on one host. There is no distributed worker scheduler.
- **Trusted local API.** SceneWorks binds to loopback by default and has no end-user login/RBAC. Do not publish the bare FastAPI service directly to an untrusted network.
- **Remote MCP requires a trusted boundary.** Use an authenticated tunnel/reverse proxy and keep the SceneWorks API itself private.

## Execution security

- **No OS sandbox.** Worktrees/path validation are engineering guard rails, not a security boundary against a hostile process. A permitted shell command runs with the authority of the OS user running SceneWorks.
- **`network_access=false` is not hard egress enforcement.** A shell-capable process can use host networking unless an OS/container/firewall boundary prevents it.
- **NativeRuntime process handles are in-memory.** `EngineeringSession` and managed PCS run metadata persist, but an OS process handle does not survive SceneWorks restart. Persisted managed PCS runs are therefore reconciled to `LOST` after restart rather than pretending control was retained.
- **External assets are read-only by contract, not a filesystem sandbox.** SceneWorks only exposes declared asset aliases through the PCS API/MCP surface; a separately permitted arbitrary shell process still has the OS user's filesystem authority.

## Agent backend limits

### Gemini ACP

- Gemini CLI authentication/model availability is external to SceneWorks.
- ACP permission mediation is stronger than prompt-only policy, but the provider runtime can have native capabilities that do not all route through the SceneWorks client proxy.
- Shell execution, once permitted, is still a real local process.

### OpenCode

- The headless backup is intended for write-capable coding/delegation.
- SceneWorks does not claim Gemini-ACP-equivalent per-tool read-only mediation for OpenCode headless mode.
- Provider/model credentials and catalogs remain owned by OpenCode.

### OpenHands

- Optional and experimental.
- Existing SDK/version and Windows shell limitations remain; OpenHands is not the SceneWorks execution substrate.
- Remote/HTTP/CLI variants are not the primary qualified backup path.

## Governed workflow limits

- **No automatic merge.** SceneWorks produces isolated branches/commits and evidence; a human remains the final integration authority.
- **In-flight autonomous executions cannot be resumed byte-for-byte after process loss.** SceneWorks records interrupted/failed state and preserves durable workflow/Git evidence rather than claiming an agent process resumed.
- **Reviewer approval is a review claim, not objective verification.** The Reviewer is independent and evidence-oriented, but its prose verdict remains model output.
- **A task can still be under-specified.** If acceptance criteria, required tests, allowed scope or project policy are absent, SceneWorks cannot manufacture objective verification requirements.

## Verification limits

WP21 now exposes criterion/test/scope/policy synthesis and an overall `PASS | FAIL | UNVERIFIABLE` result in a dedicated Verification tab. The remaining boundary is semantic verification, not missing UX.

- Acceptance criteria are free-form text. SceneWorks only treats a criterion as objectively verified when evidence is **explicitly mapped** to its `ACn` identifier and that evidence has deterministic pass/fail semantics.
- Advanced MCP `command.run` supports explicit `criterion_ids`; SceneWorks deliberately does not use fuzzy similarity or agent prose to guess mappings.
- Required tests and project go/no-go commands are matched against attributable command evidence by exact normalized command. Provider prose saying "tests passed" is insufficient.
- `allowed_scope` and `protected_paths` are deterministic only to the extent that changed-file provenance is available and the configured scope is path-like.
- Architecture invariants, dependency directions, documentation requirements, performance constraints, required review checks and release requirements remain `UNVERIFIABLE` until dedicated deterministic verifiers exist.
- A completed screenshot or visual-diff observation is evidence that pixels changed; it does not by itself prove the semantic acceptance criterion represented by the image.

See [verification-and-issue-traceability.md](verification-and-issue-traceability.md).

## Routing limits

Settings now supports both routing edges:

- role -> provider-neutral profile override;
- `strongest | coding | research` profile -> backend/model mapping.

Settings also shows each role's default/effective profile and resolved backend/model. Remaining limits:

- the available profile vocabulary (`strongest`, `coding`, `research`) is still code-defined rather than user-extensible;
- provider model availability remains external and may change independently;
- changing Settings affects future Executions only. Existing Executions retain the concrete model/backend persisted at creation time.

## Issue traceability limits

Accepted `bug`, `feature`, and `idea` tasks now receive one immutable `task.resolution` lifecycle snapshot. It preserves the authority distinction rather than pretending all fields are facts:

- resolved commit / changed files are Git-derived;
- the embedded Verification snapshot is SceneWorks-derived;
- root-cause, change-summary and remaining-risk prose are attributed Engineer/Reviewer claims.

A root-cause claim can therefore still be wrong even when the fix passes deterministic checks. SceneWorks records its provenance rather than promoting it to authoritative project knowledge.

## PCS limits

- PCS semantic control depends on project-specific run profiles/configuration being defined.
- Runtime semantic fields are only authoritative when PCS exposes them through a configured deterministic API. SceneWorks does not infer frame/playback/view state from logs.
- Health probes are intentionally loopback-only in the current PCS control contract.
- Binary crash dumps are not archived in the evidence ledger; SceneWorks stores bounded metadata/hash information.
- Large NativeRuntime output bursts can eventually stress bounded incremental output buffers; long-lived high-volume logging should use durable PCS log evidence rather than treating process-output cursors as an infinite stream.

## GUI limits

- WP17/18 are restricted to the SceneWorks-managed PCS process; they are not generic desktop automation.
- UI Automation only works for controls that expose usable Windows accessibility/UIA patterns.
- Real Windows PCS/Qt control coverage must be qualified on the actual host; Linux CI verifies the provider-neutral contract, not real Windows control accessibility.
- There is no fallback to arbitrary coordinate clicking or generic keyboard injection.
- Pixel comparison is objective visual change evidence; interpreting what a screenshot *means* is still inference unless a deterministic image verifier exists.

## Persistence

- **SQLite only.** There is no PostgreSQL/multi-instance deployment today.
- **Versioned migrations do exist.** Alembic migrations under `backend/migrations/versions/` are the schema-evolution mechanism. Older documentation claiming that SceneWorks has no migration tooling is obsolete.
- Workflow checkpoints use a separate SQLite checkpoint store.
- WP21 issue Resolution snapshots reuse the existing durable Event stream; there is no separate issue-resolution database.

## Project memory

- Project Memory retrieval is deterministic term-based retrieval, not semantic embeddings/vector search.
- It intentionally distinguishes accepted memory from proposals; model output does not become authoritative project knowledge automatically.
- It is not a general knowledge graph.

## Frontend

- The web UI is desktop-oriented. Mobile/responsive behavior is not a primary qualification target.
- No internationalization.
- The Control page is observational; engineering mutations remain in governed task/MCP/runtime paths.

## Deliberate non-goals for the current architecture

- generic desktop remote control;
- CrewAI or another orchestration framework solely to add agent roles;
- a second Jira-like ticket lifecycle;
- silent autonomous-provider failover after mutation;
- automatic merge into the user's main branch;
- treating agent/model prose as evidence;
- guessing semantic verification from textual similarity;
- duplicating provider-native capabilities without a SceneWorks governance/evidence reason.

## Highest-value next hardening

1. Real Windows PCS host qualification of WP16–WP18.
2. Add dedicated deterministic verifiers for important semantic PCS/project acceptance criteria rather than expanding heuristic inference.
3. Harden the known restart-recovery qualification timing race so the deterministic suite itself is deterministic under runner scheduling variance.
4. OS-level process/resource/network containment where a true security boundary is required.
