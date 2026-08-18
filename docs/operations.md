# Operations: versioning, migrations, backup/restore, recovery, CI

WP3 hardened SceneWorks' persistence and startup contracts. This document is
the operator-facing reference for those contracts. It states what is
implemented and verified, not what is intended.

---

## Versioning

`backend/app/__init__.py` (`__version__ = "3.0.0"`) is the single runtime
source. Everything else that reports a version reads it rather than carrying
its own copy:

| Location | How it stays in sync |
| --- | --- |
| `backend/pyproject.toml` `[project].version` | asserted equal by `tests/test_version.py` |
| FastAPI `app.version` (`/openapi.json`, docs UI) | reads `app.__version__` directly (`backend/app/main.py`) |
| `README.md` H1 title | asserted to match by `tests/test_version.py` |

**Why not `importlib.metadata`.** The backend runs as a plain `app` package
from `backend/`, not as an installed distribution, so
`importlib.metadata.version("sceneworks")` raises `PackageNotFoundError`. A
literal constant plus a guard test is the deliberate mechanism — see
`backend/app/__init__.py` for the full rationale.

**`web/package.json` (`1.0.0`) is a deliberately separate, unsynced version.**
It versions the frontend artifact, not the SceneWorks release. This is stated
here explicitly so it reads as a decision, not an oversight — found and
recorded during the WP0 audit ([wp0-baseline-audit.md](wp0-baseline-audit.md)).

Bumping the release version means changing `app.__version__` and
`pyproject.toml` together (the guard test fails if they diverge) and updating
the README title to match.

---

## Database migrations

**Tool:** Alembic. **Location:** `backend/migrations/`. **Status:
implemented, unit tested (19 tests in `tests/test_migrations.py`), and
exercised as a named step in the release-validation CI workflow.**

### What replaced what

Before WP3, `init_db()` called `Base.metadata.create_all`, which creates
missing *tables* and silently does nothing about anything else. Adding a
column to an existing database was a no-op — the application then failed
later, at query time, with no indication the schema was stale
([wp0-baseline-audit.md](wp0-baseline-audit.md), F7).

`app.db.migrations.ensure_schema()` now runs automatically in
`build_context()`, before any service touches the database, and handles three
situations explicitly:

1. **Fresh database** — no tables. Every migration runs from the start.
2. **Legacy database** — SceneWorks tables exist (`projects`, `tasks`,
   `executions`) but there is no `alembic_version` table. This is a database
   created by `create_all` before migrations existed. It is **stamped** at
   the baseline revision (`0001`, which reproduces exactly what `create_all`
   produced — verified byte-for-byte against the ORM metadata by
   `test_baseline_reproduces_what_create_all_produced`) and then upgraded.
   **It is never rebuilt.**
3. **Managed database** — `alembic_version` present. Upgraded to head.

A migration failure raises `MigrationError` with the real cause (credentials
redacted) and aborts startup, rather than letting services run against a
half-migrated schema.

### Verified non-destructive

Tested against a **copy of the actual production database** at the time of
writing — 145 projects, 112 tasks, 270 executions, 4340 events, 20 project
memories, 22 artifacts, no `alembic_version` table. After adoption: identical
row counts in every table, `project_memory.source_commit` added with `NULL`
for every pre-existing row (never a fabricated value), and the database at
head. Re-running adoption is a no-op (`tests/test_migrations.py`,
`test_running_migrations_twice_is_a_no_op`,
`test_startup_never_recreates_an_existing_database`).

### Schema history

| Revision | What it does |
| --- | --- |
| `0001` | Baseline: the schema `create_all` produced through V3.0.0 (`projects`, `tasks`, `executions`, `events`, `artifacts`, `app_settings`, `project_memory`). |
| `0002` | Adds `project_memory.source_commit` (nullable) — the repository state a decision was made against. Deferred explicitly in WP2 pending migration support; see [memory.md](memory.md). |

### Manual operation

```bash
cd backend
uv run alembic current              # what revision is this database on?
uv run alembic history              # available revisions
uv run alembic upgrade head         # apply pending migrations
uv run alembic downgrade -1         # step back one revision
uv run alembic revision -m "..."    # create a new revision
```

The database URL always comes from SceneWorks settings
(`SCENEWORKS_DATABASE_URL` / `backend/.env`), never from `alembic.ini` — this
is deliberate, so a migration cannot silently target a different database than
the one the running application uses. To operate on a specific file directly
(a copy, a backup):

```bash
uv run alembic -x db_url=sqlite:///./data/backup-copy.db upgrade head
```

**Adding a migration.** Write it by hand (`alembic revision -m "..."` scaffolds
the file) rather than via `--autogenerate`: SQLite's `ALTER TABLE` support is
limited enough that autogenerate's default operations often need hand
correction anyway, and an explicit migration is easier to review. Follow the
pattern in `0002`: a plain `ADD COLUMN` for a nullable addition; for anything
SQLite cannot alter directly (dropping/renaming a column, changing a type),
use `op.batch_alter_table(...)`, which rewrites the table under the hood —
already configured via `render_as_batch=True` in `migrations/env.py`.

### Backup and restore

SceneWorks enables SQLite **WAL mode** (`backend/app/db/session.py`), so a
running database has sidecar files: `sceneworks.db-wal` and
`sceneworks.db-shm` alongside `sceneworks.db`. Recent commits can live in the
WAL file, not yet checkpointed into the main file.

**Do not copy just the `.db` file while SceneWorks is running.** It can miss
recent commits or leave the copy in an inconsistent state. Two safe options:

```bash
# Option 1 -- SceneWorks stopped: copy all three files together.
cp data/sceneworks.db data/sceneworks.db-wal data/sceneworks.db-shm backup/
# (the -wal/-shm files may not exist if the database was cleanly closed --
# that is fine, copy whichever are present)

# Option 2 -- SceneWorks running: SQLite's online backup API is WAL-safe.
sqlite3 data/sceneworks.db ".backup 'backup/sceneworks-backup.db'"
```

**Restore:**

```bash
# Stop SceneWorks first.
cp backup/sceneworks-backup.db data/sceneworks.db
rm -f data/sceneworks.db-wal data/sceneworks.db-shm   # if present, from the old state
uv run alembic current   # confirm the restored database's revision
# Start SceneWorks; ensure_schema() upgrades it to head if the backup predates
# a migration that has since landed. This is the same adoption path a legacy
# database goes through, and is equally non-destructive.
```

A restored backup that is older than the current code's head revision is
handled exactly like any other database below head: migrated forward, not
rejected and not silently ignored.

---

## Recovery and restart semantics

Full contract: `backend/app/execution/recovery.py` (a documentation module —
the behaviour lives in `ExecutionEngine.recover_interrupted()`,
`ExecutionEngine.shutdown()`, and `WorkflowManager.recover_workflows()`).
Regression coverage: `backend/tests/test_recovery.py` (21 tests). Integration
coverage: the `restart-recovery` qualification scenario
([qualification.md](qualification.md)).

**The invariant:** after a restart, no task may claim work is in progress.
Nothing is executing and no graph survived, so a task left in a running state
(`ARCHITECTURE_ANALYSIS`, `IMPLEMENTING`, `REVIEWING`) is a false statement
about the project — an operator cannot tell whether to wait or to retry.

Two reconciliation passes run on every startup:

1. **Executions still marked active** (`QUEUED`/`STARTING`/`RUNNING`) — the
   process died without unwinding. Marked `INTERRUPTED`.
2. **Tasks claiming to run with nothing active** — a *clean* shutdown
   finalizes its own executions, so pass 1 finds nothing; without pass 2 such
   a task stayed in `ARCHITECTURE_ANALYSIS` forever, with no agent, no graph,
   and no retry path. Marked `FAILED`, which `retry` accepts.

Human-waiting states (`AWAITING_ARCHITECTURE_APPROVAL`, `CHANGES_REQUESTED`,
`READY_FOR_HUMAN`, `ACCEPTED`, `REJECTED`) are left untouched — nothing about
them depends on a live process.

What survives explicitly: committed work (`result_commit`, task branch), the
engineer worktree on disk (reclaimed lazily when next needed, never
overwritten), and — for every task — a state that either waits for a human or
accepts `retry`.

---

## Continuous integration

Two workflows, split by cost, matching the CI policy agreed for this roadmap:

| Workflow | Trigger | Runs |
| --- | --- | --- |
| `.github/workflows/ci.yml` | every push to `master` | import/startup check, `pytest -m "not slow and not live"`, qualification `--smoke`, frontend `next build` (type-check + lint + build) |
| `.github/workflows/release.yml` | manual dispatch, or a `v*` tag push | full `pytest -m "not live"` (includes `slow` workflow tests), migration validation (fresh + legacy adoption, via `tests/test_migrations.py`), full deterministic qualification (19 scenarios), Playwright E2E against a live backend |

**Why the split.** The full backend suite plus full qualification take
roughly 10 minutes combined (measured: suite ~580s, qualification ~260s) —
too slow to gate every push. SceneWorks develops directly on `master`
(no PR-based workflow — see [development.md](development.md)), so this is a
push-triggered gate, not a PR check.

**What CI never runs, by design.** Tests marked `live` (real agent provider,
real model) are excluded from both workflows — a GitHub-hosted runner has no
Gemini CLI authentication and no local LLM endpoint, and they must skip
cleanly rather than fail or fall back to a fake
([wp2.5-openhands-validation.md](wp2.5-openhands-validation.md)). Live
qualification against a real backend
(`python -m evaluation --backend openhands|gemini_acp ...`) is a manual, local
operation — never part of an automated gate, so a live PASS can never be
mistaken for a release gate ([qualification.md](qualification.md)).

Both workflows install the base dependency set only — never the `openhands`
extra, which moves pre-existing pins (including a pydantic downgrade) and has
no place gating routine pushes.
