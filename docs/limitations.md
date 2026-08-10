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
