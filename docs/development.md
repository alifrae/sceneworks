# Development

## Developer setup

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (Python dependency manager)
- Node.js >= 20 with npm
- Git

### Clone and install

```bash
git clone <repo-url> sceneworks
cd sceneworks

# Backend
cd backend
uv sync

# Frontend
cd ../web
npm install
```

### Run in development

**Backend** (terminal 1):
```bash
cd backend
uv run uvicorn app.main:app --reload
```

**Frontend** (terminal 2):
```bash
cd web
npm run dev
```

Open http://localhost:3000

### Running tests

```bash
cd backend
uv run pytest                    # all tests
uv run pytest tests/test_api.py  # specific test file
uv run pytest -k "openhands"     # tests matching pattern
uv run pytest --collect-only     # list all tests
```

The test suite uses an in-memory SQLite database and does not require any
live services or model access.

### Code conventions

- **No comments** unless necessary for architectural clarity
- Follow existing patterns in each module
- Async-first: all I/O is `async def`
- Type hints on all function signatures
- No bare exceptions — always specify exception types
- Event payloads are plain JSON dicts
- Use `from __future__ import annotations` in all Python files

### Project structure conventions

| Concern | Pattern |
|---|---|
| API routes | `app/api/<resource>.py`, uses FastAPI dependency injection |
| Domain logic | Stateless functions, no I/O |
| Services | Stateful classes with `_session_factory` for DB access |
| Agents | Single-file adapter implementing `AgentBackend` protocol |
| Events | Constants in `types.py`, labels for UI, store for persistence |
| Git | Subprocess-based via `asyncio.create_subprocess_exec` |

### Adding a new role

1. Add `RoleDefinition` in `backend/app/roles/definitions.py`
2. Add `backend/app/roles/prompts/<key>.md` with instructions
3. Expose in the company API automatically

### Adding a new workflow node

Workflow nodes live in `backend/app/workflows/manager.py` as methods on
`WorkflowManager`. They are thin adapters that:
1. Receive LangGraph state
2. Delegate to TaskWorkflowService or ExecutionEngine
3. Return updated state or LangGraph `Command`

### Adding a new API route

1. Create or extend a module in `backend/app/api/`
2. Register the router in `backend/app/main.py`
3. Add Pydantic schemas in `backend/app/schemas.py` if needed
4. Add SQLAlchemy models in `backend/app/models.py` if needed

### Adding a new event type

1. Add constant in `app/events/types.py`
2. Add label in `EVENT_LABELS` dict
3. Emit through `EventStore.append()` or `EventBus.publish()`

### Frontend development

The frontend is a Next.js 15 App Router application with TypeScript.

- Pages: `web/app/<route>/page.tsx`
- Shared components: `web/components/`
- API client: `web/lib/api.ts`
- Types: `web/lib/types.ts`
- Styles: `web/app/globals.css`

Run the linter:
```bash
cd web
npm run lint
```
