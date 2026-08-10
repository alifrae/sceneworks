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
- **OpenHands HTTP mode**: When connecting to a remote OpenHands Agent
  Server, workspace isolation depends on the server's configuration.
  SceneWorks cannot enforce file boundaries over HTTP.

### Execution

- **Sequential ACP backends**: Two Gemini ACP instances may not run
  concurrently on Windows due to Gemini's single-instance lock. Multiple
  executions queue up on the single worker.
- **No parallel Engineers**: Only one Engineer execution runs at a time
  per task.
- **No competing Architects**: Architecture analysis runs once.

### Workflow

- **Project memory is SQLite-backed text search**: V2.4 adds persistent
  project memory with metadata/tag-based retrieval. No embeddings, no
  vector database, no semantic search.
- **No knowledge graph**: No persistent semantic understanding between
  sessions beyond explicit memory items.
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
- **Worktrees leave `fsmonitor` daemons behind.** If a managed repository has
  `core.fsmonitor=true`, git starts a long-lived `git fsmonitor--daemon` for
  each worktree, and removing the worktree does not reliably reap it. Across
  one afternoon of testing on a repository with fsmonitor enabled, **308**
  orphaned `git.exe` processes accumulated and slowed every git operation on
  the machine (a trivial `init`+`commit` went from sub-second to ~3 s), which
  in turn caused unrelated test-suite timeouts. SceneWorks does not manage
  these daemons. If you run many tasks against an fsmonitor-enabled
  repository, either disable it for that repository
  (`git config core.fsmonitor false`) or reap the daemons periodically
  (`taskkill /F /IM git.exe` on Windows, `git fsmonitor--daemon stop` per
  worktree).
- **Concurrent live agents are resource-hungry.** Two Gemini CLI processes
  starting simultaneously exceeded the previous 30 s ACP `initialize` timeout
  on this machine. The default is now 120 s, but throughput remains bounded by
  agent process startup, which is slow on Windows.

### OpenHands backend specifics

- **Experimental/unvalidated**: The OpenHands backend has not been tested
  against a live OpenHands Agent Server. SDK imports reflect documented API
  but have not been verified end-to-end.
- **SDK/WebSocket mode (preferred)**, HTTP polling (compatibility fallback),
  CLI/headless (development fallback only).
- **Server dependency**: SDK and HTTP modes require a separately running
  OpenHands Agent Server. SceneWorks does not manage the OpenHands lifecycle.
- **Event polling**: HTTP mode polls for conversation status rather than
  receiving streaming updates. This adds latency compared to Gemini's ACP
  streaming.
- **API version compatibility**: The OpenHands API is under active
  development. The adapter targets the current documented API surface.
  Custom API endpoints may require adapter modifications.
- **No ACP-level permission mediation**: Unlike Gemini ACP where every
  file read/write is approved per-request, OpenHands relies on workspace
  directory scoping via configuration.

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
- **No migration tooling**: Database schema is created from SQLAlchemy
  models with no versioned migrations.

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
