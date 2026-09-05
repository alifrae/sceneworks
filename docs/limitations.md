# Known Limitations

This file describes the **current** SceneWorks boundary after WP21. Historical V2/V3/WP documents should not be used as the current limitations list.

## Deployment and trust

- **Single-machine control plane.** FastAPI, workflow execution, SQLite, local runtimes, the lifecycle supervisor and agent processes run on one host. There is no distributed worker scheduler.
- **Trusted local API.** SceneWorks binds to loopback by default and has no end-user login/RBAC. Do not publish the bare FastAPI service directly to an untrusted network.
- **Local lifecycle supervisor only.** WP21 binds the supervisor to `127.0.0.1:8020` and uses a local bearer token for mutations. It is not a remote-management endpoint and must not be published directly.
- **Remote MCP requires a trusted boundary.** Use an authenticated tunnel/reverse proxy and keep the SceneWorks API itself private.

## Execution security

- **No OS sandbox.** Worktrees/path validation are engineering guard rails, not a security boundary against a hostile process. A permitted shell command runs with the authority of the OS user running SceneWorks.
- **`network_access=false` is not hard egress enforcement.** A shell-capable process can use host networking unless an OS/container/firewall boundary prevents it.
- **NativeRuntime process handles are in-memory.** `EngineeringSession` and managed PCS run metadata persist, but an OS process handle does not survive SceneWorks restart. Persisted managed PCS runs are therefore reconciled to `LOST` after restart rather than pretending control was retained.
- **External assets are read-only by contract, not a filesystem sandbox.** SceneWorks only exposes declared asset aliases through the PCS API/MCP surface; a separately permitted arbitrary shell process still has the OS user's filesystem authority.

## Infrastructure lifecycle limits

WP21 gives SceneWorks durable local lifecycle ownership for `api`, `web`, and `mcp_tunnel`, but it deliberately stops short of remote administration or generic process supervision.

- **Fixed component set.** The supervisor cannot launch arbitrary executables or manage arbitrary ports/processes. New lifecycle targets require an explicit semantic component contract, health probe, startup grace, ownership proof, bounded recovery policy, and tests.
- **Windows is the primary host target.** CI validates the Windows process provider, launcher parsing, and deterministic lifecycle contracts, but destructive recovery behavior still needs final qualification on the actual SceneWorks laptop/process tree.
- **Supervisor availability remains a dependency.** If the supervisor process itself exits, API/web/tunnel automatic recovery is unavailable until the launcher starts the supervisor again. WP21 does not install a Windows Service or OS-level watchdog for the supervisor itself.
- **Bootstrap configuration is process-lifetime configuration.** `-Dev` and `-NoTunnel` affect a newly started supervisor. An already-running supervisor retains the configuration it was started with; changing those flags requires restarting the supervisor process.
- **Legacy adoption is deliberately conservative.** Existing pre-WP21 processes are adopted only when both the fixed listener and expected command fingerprint match. Ambiguous cases remain `UNKNOWN` rather than being forcefully taken over.
- **Recovery is bounded, not infinite.** Three attempts in a rolling five-minute window lead to `DEGRADED`; this is intentional crash-loop protection, not an availability defect.
- **No remote hub/edge recovery yet.** A future distributed/remote recovery design must use a new trust boundary rather than exposing the loopback supervisor remotely.

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

## Verification UX gap

SceneWorks has substantial verification infrastructure but does not yet expose the complete task-level synthesis originally envisioned:

- acceptance criterion -> objective test/evidence -> result;
- required test PASS/FAIL presentation;
- allowed-scope/protected-path policy compliance presentation;
- explicit overall `PASS | FAIL | UNVERIFIABLE` result;
- dedicated **Verification** tab on the task page.

WP15–WP18 make this feasible because evidence is now durable and correlated. The missing work is the synthesis/UX layer, not another evidence store.

See [verification-and-issue-traceability.md](verification-and-issue-traceability.md).

## Routing UX gap

Settings already supports:

- selecting the default autonomous worker;
- backend health/status;
- `strongest | coding | research` profile -> backend/model mapping;
- persisted concrete backend/model on each `Execution`;
- explicit no-silent-fallback behavior.

The remaining routing gap is that **role -> profile assignment is still code configuration** in `backend/app/roles/definitions.py`. For example, Engineer defaults to `coding`, while Architect and Reviewer default to `strongest`. It is not currently editable from Settings.

Settings also does not yet present one compact per-role row containing default profile, effective profile, resolved backend, resolved concrete model and source of inheritance/override.

## Issue traceability gap

The WP20 Issues page correctly reuses the existing Task domain (`bug | feature | idea`) instead of creating a second Jira-like database. However, closed issues do not yet have a first-class structured resolution snapshot.

Recommended fields are:

- root-cause claim;
- fix/change summary;
- objective verification result and evidence references;
- changed commit/files;
- remaining risk / unverifiable areas.

The root-cause text is an engineering claim unless supported by evidence. Fix/verification metadata should be re-derived from SceneWorks Git/evidence rather than copied blindly from an agent response.

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
- WP21 lifecycle operations use a separate supervisor SQLite journal so acceptance/recovery history survives FastAPI restarts.

## Project memory

- Project Memory retrieval is deterministic term-based retrieval, not semantic embeddings/vector search.
- It intentionally distinguishes accepted memory from proposals; model output does not become authoritative project knowledge automatically.
- It is not a general knowledge graph.

## Frontend

- The web UI is desktop-oriented. Mobile/responsive behavior is not a primary qualification target.
- No internationalization.
- The WP20 Control page is observational; engineering mutations remain in governed task/MCP/runtime paths.
- WP21 Diagnostics can request semantic infrastructure restarts, but lifecycle credentials remain server-side and the browser is not a general process-control client.

## Deliberate non-goals for the current architecture

- generic desktop remote control;
- generic/remote process administration through the WP21 supervisor;
- CrewAI or another orchestration framework solely to add agent roles;
- a second Jira-like ticket lifecycle;
- silent autonomous-provider failover after mutation;
- automatic merge into the user's main branch;
- treating agent/model prose as evidence;
- duplicating provider-native capabilities without a SceneWorks governance/evidence reason.

## Highest-value next hardening

1. Real Windows host qualification of WP21 recovery/ownership plus WP16–WP18 PCS/GUI behavior.
2. First-class task Verification synthesis/UX.
3. Structured issue-resolution snapshots backed by SceneWorks evidence.
4. Editable role -> model-profile mapping plus explicit resolved-routing display.
5. Decide whether supervisor self-watch/Windows-Service integration is justified after laptop qualification; do not add it preemptively.
6. OS-level process/resource/network containment where a true security boundary is required.
