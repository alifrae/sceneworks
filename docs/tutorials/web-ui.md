# Web UI Tutorial

Step-by-step guide from installation to accepting a task result.

## 1. Prerequisites

- **Python 3.12+** (via `uv`, the included Python dependency manager)
- **Node.js >= 20** with `npm`
- **Git** (must be on PATH)
- **Windows:** PowerShell or Windows Terminal
- **macOS/Linux:** Terminal

For Gemini ACP backend (optional): [Gemini CLI](https://github.com/google-gemini/gemini-cli)
installed and authenticated.

For OpenHands backend (optional):
[OpenHands Agent Server](https://github.com/OpenHands/openhands) running
and accessible.

## 2. Backend installation

```bash
cd backend
uv sync
```

## 3. Frontend installation

```bash
cd web
npm install
```

## 4. Configuration

Copy the example env file to `backend/.env`:

```bash
# Windows
copy .env.example backend\.env

# macOS/Linux
cp .env.example backend/.env
```

Edit `backend/.env` to customize:

```env
SCENEWORKS_HOST=127.0.0.1
SCENEWORKS_PORT=8010
SCENEWORKS_DATABASE_URL=sqlite+aiosqlite:///./data/sceneworks.db
SCENEWORKS_WORKTREE_ROOT=C:/Users/you/sceneworks-worktrees   # Windows
# SCENEWORKS_WORKTREE_ROOT=/home/you/sceneworks-worktrees    # Linux/macOS
SCENEWORKS_EXECUTION_TIMEOUT_SECONDS=1800
SCENEWORKS_LOG_LEVEL=INFO
SCENEWORKS_CORS_ORIGINS=["http://localhost:3000"]
```

### Gemini ACP configuration (default backend)

By default, SceneWorks auto-discovers `gemini` on PATH. Optional overrides:

```env
SCENEWORKS_GEMINI_EXECUTABLE=gemini
SCENEWORKS_GEMINI_MODEL=gemini-2.5-pro
```

### OpenHands configuration (optional)

```env
SCENEWORKS_OPENHANDS_URL=http://localhost:8000
# or for CLI mode:
# SCENEWORKS_OPENHANDS_EXECUTABLE=openhands
SCENEWORKS_OPENHANDS_MODEL=claude-sonnet-4-20250514
```

### Default backend selection

```env
SCENEWORKS_DEFAULT_BACKEND=gemini_acp   # default
# SCENEWORKS_DEFAULT_BACKEND=openhands  # use OpenHands
# SCENEWORKS_DEFAULT_BACKEND=fake       # tests/demos only
```

## 5. Starting the FastAPI backend

```bash
cd backend
uv run uvicorn app.main:app --reload
```

The API is available at **http://127.0.0.1:8010**
API documentation at **http://127.0.0.1:8010/docs**

## 6. Starting the Next.js frontend

Open a **second terminal**:

```bash
cd web
npm run dev
```

The web UI is available at **http://localhost:3000**

## 7. Opening SceneWorks in the browser

Navigate to **http://localhost:3000**

You should see the SceneWorks dashboard with navigation:
- **Dashboard** — Overview of projects, tasks, active executions
- **Projects** — Manage registered repositories
- **Tasks** — Create and manage tasks
- **Executions** — View past agent executions
- **Company** — View the virtual company org chart
- **Settings** — Configure backends and view system settings
- **Docs** — API documentation (links to `/docs`)

## 8. Registering a repository

1. Navigate to **Projects**
2. Click **Add Repository**
3. Enter:
   - **Name**: A friendly name (e.g. "my-app")
   - **Description**: Optional description
   - **Repository path**: Absolute path to a local Git repository
     - Windows: `C:\Users\you\projects\my-app`
     - macOS/Linux: `/home/you/projects/my-app`
4. Click **Register**

SceneWorks validates that the path exists and is a valid Git repository.
It captures the current branch and HEAD commit. **Your repository files
are never modified.**

## 9. Creating a task

1. Navigate to **Tasks**
2. Click **New Task**
3. Select the registered project
4. Enter a **title** (e.g. "Fix incorrect calculation in component X")
5. Enter a **description** (what should be changed or built)
6. Optionally set priority (low/medium/high)
7. Click **Create**

The task appears with status **NEW**.

## 10. Running the workflow

### Option A: Full workflow (recommended)

1. Open the task detail page
2. Click **Start Architecture**
   - The Architect inspects your repository (read-only)
   - Watch live events in the **Event Log** panel
3. Wait for the task to reach **Awaiting Architecture Approval**
4. Review the architecture analysis
5. Click **Approve Architecture**
   - The LangGraph workflow automatically runs:
     1. Engineer implements changes in an isolated worktree
     2. (Optional) Tests are executed
     3. Reviewer inspects the diff and approves or requests changes
     4. If changes requested, Engineer is auto-reinvoked (repair loop)
6. Wait for **Ready for Human Review**

### Option B: Step-by-step manual

1. Click **Start Architecture** → Architecture analysis runs
2. Review analysis → Click **Approve** (or Reject / Request Revision)
3. Click **Start Implementation** → Engineer implements in worktree
4. Click **Start Review** → Reviewer inspects diff/tests
5. Arrive at **Ready for Human Review**

## 11. Approving architecture

After architecture analysis completes:

1. Read the architecture analysis on the task detail page
2. Choose:
   - **Approve** — Proceed to implementation
   - **Reject** — Cancel the task
   - **Request Revision** — Send back to Architect with feedback

## 12. Following live execution

During execution, the **Event Log** panel streams events in real-time:

- `execution.started` — Agent invocation began
- `agent.text_delta` — Agent's text output (streaming)
- `agent.thought_summary` — Agent's reasoning summary
- `tool.started` / `tool.completed` — Tool usage (file reads, commands)
- `file.changed` — File was modified
- `git.commit` — Commit was created
- `execution.completed` — Agent finished

## 13. Inspecting diff, tests, and review

When the task reaches **Ready for Human Review**:

1. View the **diff** — All changes made by the Engineer
2. View the **implementation summary** — What was done
3. View the **review result** — Reviewer verdict (APPROVED or feedback)
4. View **test results** if tests were configured for the project

## 14. Accepting or rejecting the final result

1. Review the diff, summary, and review verdict
2. Choose:
   - **Accept** — Mark the task as accepted. SceneWorks **does not merge**
     automatically. You decide what to integrate from the worktree branch
     (`sw-task-<id>`).
   - **Reject** — Reject the implementation. The task enters `REJECTED`.
   - **Send back to Engineer** — Request additional changes.

## 15. Selecting/configuring an agent backend

### Per-role backend selection

Each role can use a different backend. Edit
`backend/app/roles/definitions.py`:

```python
RoleDefinition(
    key="engineer",
    backend="openhands",  # Use OpenHands for implementation
    ...
)
```

Or via the Settings page in the web UI (if supported by current version).

### Global default

Set `SCENEWORKS_DEFAULT_BACKEND` in `backend/.env`:

```env
SCENEWORKS_DEFAULT_BACKEND=openhands
```

Available backends:
- `gemini_acp` — Gemini CLI via ACP (default)
- `openhands` — OpenHands Agent Server
- `fake` — Scripted fake backend (tests/demos only)

### Verifying backend health

Navigate to **Settings** in the web UI. Each backend shows its availability
status, version, and details.

## 16. Common troubleshooting

### "Gemini CLI not found on PATH"

- Install Gemini CLI: `npm install -g @google/gemini-cli`
- Or set `SCENEWORKS_GEMINI_EXECUTABLE` to the full path of the gemini
  executable

### "OpenHands not configured"

- Set `SCENEWORKS_OPENHANDS_URL` for HTTP mode or
  `SCENEWORKS_OPENHANDS_EXECUTABLE` for CLI mode
- For HTTP mode, ensure the OpenHands Agent Server is running

### Backend starts but task stays "NEW"

- Check that a backend is available (Settings page)
- Check the backend health status
- Ensure the default backend matches your configuration

### "Port 8010 already in use"

- Change `SCENEWORKS_PORT` in `backend/.env`
- Update `NEXT_PUBLIC_API_URL` in the frontend accordingly

### "CORS error" in the browser

- Ensure `SCENEWORKS_CORS_ORIGINS` includes `http://localhost:3000`
- Restart the backend after changing CORS settings

### Task stuck in "ARCHITECTURE_ANALYSIS"

- The backend may have crashed. Check the terminal running the FastAPI
  server for errors.
- On Windows: Gemini CLI sometimes needs a console window. Ensure
  `SCENEWORKS_GEMINI_EXECUTABLE` points to the correct executable.
- Try using the fake backend (`SCENEWORKS_DEFAULT_BACKEND=fake`) to verify
  the workflow machinery works.

### Windows-specific issues

- Gemini CLI is spawned with `CREATE_NEW_PROCESS_GROUP` and
  `CREATE_NEW_CONSOLE` flags. A console window may briefly appear.
- Paths use backslashes but SceneWorks handles both `\` and `/`.
- Worktree paths must be on the same drive as the repository.
- If `git` is not on PATH, add it or use full paths.

### macOS/Linux-specific notes

- All commands use `uv run` — no manual virtual environment activation needed
- Path separators are forward slashes `/`
- No extra console windows appear for subprocesses

### Worktree errors

- `SCENEWORKS_WORKTREE_ROOT` must be on the same filesystem/drive as the
  repository (Git worktree limitation)
- The worktree root must not be inside a managed repository
- If a worktree already exists for a task (duplicate start), it is reused
