# Known Limitations

## Current limitations (V2.5)

### Deployment

- **Single-machine**: API + worker + SQLite run together on one machine. No
  distributed execution support.
- **Localhost only**: The API binds to localhost by default — not designed
  for remote access without a reverse proxy and authentication.

### Security

- **No user authentication**: The API is a trusted local control plane with
  no login, accounts, or teams.
- **No OS-level sandboxing**: Agent commands run with the SceneWorks
  process's user permissions. Isolation is through Git worktrees and
  backend-enforced path boundaries, not OS containers.
- **OpenHands**: workspace confinement is directory scoping by the agent's own
  tools, not an OS or container boundary, and SceneWorks cannot enforce file
  boundaries per-request as it does over ACP. For a remote Agent Server the
  `working_dir` is a path in the *server's* filesystem, so it does not even refer
  to the SceneWorks worktree. See the OpenHands section below.

### Execution

- **Sequential ACP backends**: Two Gemini ACP instances may not run
  concurrently on Windows due to Gemini's single-instance lock. Multiple
  executions queue up on the single worker.
- **No parallel Engineers**: Only one Engineer execution runs at a time
  per task.
- **No competing Architects**: Architecture analysis runs once.

### Workflow

- **Project memory retrieval is term-based, not semantic**: scoring matches
  *terms* against title, content and tags, with an exact-phrase bonus. No
  embeddings, no vector database, no semantic search — so a memory phrased
  entirely in synonyms of the task description will not be retrieved
  ("point cloud" will not find a decision that only says "LiDAR scan"). The
  tradeoff is deliberate: retrieval is reproducible and every result explains
  why it was selected. SQLite FTS5 is the documented upgrade path if the term
  bound becomes the limit; see [memory.md](memory.md).
- **A memory's `source_commit` column exists (WP3) but nothing populates it
  yet**: `propose_from_execution()`, the intended entry point for memories
  extracted from agent output, is not called from any workflow node. Wiring
  a real commit into automatic memory creation is WP6 scope (Git provenance).
- **No knowledge graph**: No persistent semantic understanding between
  sessions beyond explicit memory items.
- **Policy enforcement is deterministic for one category only**: SceneWorks
  itself checks `protected_paths` against the Engineer's actual diff. Every
  other policy category (architecture invariants, forbidden dependency
  directions, documentation/performance/release requirements, required review
  checks) is judged by the Reviewer's own reading of the labelled policy
  block — SceneWorks cannot mechanically verify "the changelog was updated
  appropriately" the way it can verify "was this exact path touched."
  Inventing a mechanical check for those categories would be exactly the false
  precision this roadmap exists to avoid. See [project-policy.md](project-policy.md).
- **`go_no_go_commands` are declarative, not executed**: they are surfaced to
  roles as the project's release-qualification suite; nothing in SceneWorks
  runs them automatically.
- **No policy management UI**: configuration is API-only
  (`GET`/`PUT`/`DELETE /api/projects/{id}/policy`). Deliberate — a
  resource-centric CRUD screen is exactly what the roadmap says not to build
  before the conversation-first model is extended (WP5 scope).
- **No CrewAI integration**: SceneWorks uses its own LangGraph-based
  orchestration.
- **No automatic merging**: SceneWorks never merges agent branches into
  the human tree. All integration is manual.
- **Recovery limitations**: Workflows with in-flight agent executions
  (`ARCHITECTURE_ANALYSIS`, `IMPLEMENTING`, `REVIEWING`) cannot be
  recovered on restart — the in-progress agent work is unrecoverable.
  These tasks are marked `FAILED`. Workflows in human-waiting or
  between-node states can auto-resume. See `backend/app/execution/recovery.py`
  for the full state-by-state contract.

### Accepted V2 limitations carried into the V3.0 baseline

These were found during the V3.0 audit, judged not worth changing now, and
are deliberately carried forward.

- **Mediation is partial, and shell access is not confined.** SceneWorks
  confines `fs/read_text_file` / `fs/write_text_file` to the pinned worktree,
  gates `session/request_permission` on the role, and validates the working
  directory of any terminal the agent opens. Measured over the V3.0 live runs,
  28 of 191 observed tool calls actually reached the permission gate — the
  rest were reported as `session/update` notifications for tools the agent ran
  through its own runtime rather than asking the client. The gate does work
  when used (it refused `git reflog`, `git remote -v` and
  `python -m pytest --version` for read-only roles), but coverage depends on
  the agent asking. Separately, a role holding `shell_execute` (Engineer,
  Reviewer, Technical Expert) runs real commands, and a command can `cd`
  anywhere the OS permits. Treat this as a guard rail and an audit trail,
  **not a sandbox against a hostile agent**. Registered repositories are
  trusted inputs; an agent with shell access has the privileges of the user
  running SceneWorks.
- **Large repositories are slow and can exhaust the timeouts.** Creating a
  worktree checks out the entire tree (minutes on a ~30k-file repository), and
  an Engineer run spends most of its wall time in the project's own test and
  lint commands. The V3.0 defaults (`SCENEWORKS_GIT_TIMEOUT_SECONDS=900`,
  `SCENEWORKS_EXECUTION_TIMEOUT_SECONDS=5400`) were raised after real runs hit
  the old ceilings. If an Engineer execution does time out, the task goes
  `FAILED` with its work left **uncommitted in the isolated worktree** — it is
  not lost, but it is not captured as a commit either, and `retry` starts a
  fresh execution rather than resuming that one. Scoping the task ("run only
  this test file, do not run repository-wide lint") materially reduces the
  risk.
- **`worktree_root_override` is stored but never honoured.** The field exists
  on `Project`, is accepted by the API and returned by it, but
  `GitWorktreeService` only reads the global `worktree_root`. Per-project
  worktree roots are not implemented.
- **Settings changes are partly runtime, partly restart-only.** Values read
  live from the shared `Settings` object (worktree root, Gemini executable and
  model, execution timeout) and the default backend now take effect
  immediately. Anything captured at construction time elsewhere still needs a
  restart.
- **No authentication or authorisation.** Anyone who can reach the API can
  approve architecture and accept work. Bind to localhost only.
- **A failed triage degrades to default routing.** If the triage execution does
  not complete, the workflow proceeds with `architect` selected and
  `requires_implementation=true`. This is surfaced as a
  `workflow.triage.degraded` event and a task note rather than failing the
  task, because the architecture approval gate still stands between that guess
  and any code change — but the participant selection was not actually
  decided by triage.
- **Agents sometimes finish without committing.** Observed with a live model:
  the Engineer edited files and ended its turn without running `git commit`.
  SceneWorks now commits leftover worktree changes on the Engineer's behalf,
  using `git add -A`. If the repository lacks a `.gitignore`, build artefacts
  produced during the run can be swept into that safety-net commit. The human
  reviews the diff before integrating.
- **Worktrees previously leaked `fsmonitor` daemons.** If a managed repository had
  `core.fsmonitor=true`, git started a long-lived `git fsmonitor--daemon` for
  each worktree, and removing the worktree did not reliably reap it. Across
  one afternoon of testing on a repository with fsmonitor enabled, **308**
  orphaned `git.exe` processes accumulated and slowed every git operation.
  **Fixed in V2.5.2:** SceneWorks git operations and agent terminal commands
  now suppress fsmonitor per-process via `GIT_CONFIG_PARAMETERS`. The user's
  global and repository-level `core.fsmonitor` configuration is never modified.
  A stress test of 20 worktree create/destroy cycles confirms no unbounded
  daemon accumulation.
- **Concurrent live agents are resource-hungry.** Two Gemini CLI processes
  starting simultaneously exceeded the previous 30 s ACP `initialize` timeout
  on this machine. The default is now 120 s, but throughput remains bounded by
  agent process startup, which is slow on Windows.

### OpenHands backend specifics

Status **EXPERIMENTAL**. `local` mode is live-validated for read-only roles;
full evidence in [wp2.5-openhands-validation.md](wp2.5-openhands-validation.md).

- **No shell on Windows — the Engineer cannot run.** The OpenHands V1 terminal
  tool raises `NotImplementedError` on Windows
  (`openhands/tools/terminal/terminal/factory.py`). Roles needing shell are
  refused up front with the reason. Only read-only roles are usable on this
  platform. Gemini ACP is unaffected and remains the default.
- **No OS-level sandbox.** In `local` mode the agent runs inside the SceneWorks
  process with the worktree as its working directory. Confinement is the tools'
  own path handling, not a container, chroot or user boundary. The runtime and
  the model are trusted not to write outside the working directory — verified
  empirically for the validated runs, not enforced structurally.
- **No per-request permission mediation.** Unlike Gemini ACP, where every file
  read/write and every shell command passes through the ACP proxy and can be
  refused individually, OpenHands offers directory scoping only. This is strictly
  weaker enforcement.
- **`remote` mode is unvalidated and has a path-domain problem.** `working_dir` is
  a path in the *Agent Server's* filesystem. A server in Docker or WSL cannot see
  a Windows SceneWorks worktree, so commit-pinned isolation cannot be established
  that way. Making remote mode work needs design (shipping or mounting the
  worktree), not configuration.
- **`http` and `cli` modes are implemented but unvalidated.**
- **A model is mandatory.** `SCENEWORKS_OPENHANDS_MODEL` must be set (litellm
  form); the SDK rejects an unspecified model and health reports unavailable.
- **Version pinning is not optional.** `openhands-sdk` and `openhands-tools` must
  be the same version: a mismatched pair installs cleanly and then fails at
  import. The extra pins the validated 1.17.0 pair.
- **Newer SDK releases currently cannot be installed.** `openhands-sdk` pulls
  `lmnr`, which pins `opentelemetry-semantic-conventions==0.60b1` while
  `opentelemetry-instrumentation` pins its own matching version; no combination
  satisfies both (pip: `ResolutionImpossible`). Upstream issue.
- **Installing the extra moves shared pins.** It adds ~150 packages and downgrades
  pydantic (2.13.4 → 2.12.5) among others. The suite passes afterwards, but this
  is why OpenHands is an optional extra rather than a default dependency.
- **A hung run can outlive its timeout.** The SDK is synchronous and runs in a
  worker thread, which cannot be forcibly killed; `pause()` is cooperative and is
  observed between turns. A single LLM call with litellm's retry policy can take
  minutes. `SCENEWORKS_OPENHANDS_MAX_ITERATIONS` (default 40) bounds the turn
  count so a non-converging model finishes with partial output.
- **No structured test results.** OpenHands reports no test outcome, so
  `test.result` events are not produced rather than fabricated.

### Gemini ACP backend specifics

- **Single-user profile**: On Windows, two Gemini instances may conflict
  when run from the same user profile simultaneously.
- **Console window**: On Windows, Gemini CLI requires a console window
  for its shell tool. A window may briefly appear during execution.
- **ACP v1 only**: The backend implements ACP protocol v1. Newer
  protocol versions would require adapter updates.

### Git worktrees

- **Same filesystem**: Git worktrees must be on the same filesystem/drive
  as the main repository (Git limitation).
- **Windows path length**: Very deep worktree paths may exceed Windows
  MAX_PATH (260 characters). Use `SCENEWORKS_WORKTREE_ROOT` with short paths.

### Database

- **SQLite only**: No PostgreSQL, MySQL, or other database backends.
- **Versioned migrations (WP3)**: schema changes go through Alembic
  (`backend/migrations/`), applied automatically at startup
  (`app.db.migrations.ensure_schema`). A pre-migrations database is adopted
  by stamping it at the baseline revision, never rebuilt -- verified against
  a copy of a real production database with 145 projects, 112 tasks, 270
  executions and 4340 events, all preserved. See
  [operations.md](operations.md).

### Frontend

- **No major UI redesign**: The current UI is functional but not heavily
  styled — it prioritizes correctness over visual design.
- **No responsive mobile layout**: Designed for desktop browser use.
- **No internationalization**: English only.

## Non-goals (not planned for V2)

- RAG / embeddings / vector DB
- Knowledge graph
- CrewAI integration
- PostgreSQL or other database backends
- Remote worker infrastructure
- GitHub PR / auto-merge
- Parallel Engineers or competing Architects
- Major UI redesign
- User authentication / multi-tenancy

## Future directions

- Remote workers for distributed execution
- OCI container-based sandboxing
- Broker-backed event bus (Redis, NATS)
- PostgreSQL for multi-instance deployments
- User accounts and team sharing
- GitHub/GitLab PR integration
