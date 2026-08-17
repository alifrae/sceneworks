"""SceneWorks API entry point.

Trust assumptions (V1):
- The API binds to localhost by default; it is a trusted control plane, not
  a public service.
- Agents run on the same machine as the API, inside isolated Git worktrees.
- There are no user accounts or RBAC: anyone who can reach the API can
  approve tasks. Do not expose it beyond localhost.
- Repositories registered by the user are trusted inputs; agent output is
  always reviewable before integration (SceneWorks never merges).
"""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.requests import Request

from app.api import (
    backends_router,
    company_router,
    dashboard_router,
    events_router,
    executions_router,
    memory_router,
    projects_router,
    roles_router,
    settings_router,
    tasks_router,
)
from app.config.settings import get_settings
from app.context import build_context


class ContextFilter(logging.Filter):
    """Appends structured identifiers (task_id, execution_id, ...) to logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        extra = []
        for key in ("task_id", "execution_id", "project_id", "role", "backend"):
            value = getattr(record, key, None)
            if value is not None:
                extra.append(f"{key}={value}")
        if extra:
            record.msg = f"{record.msg} [{', '.join(extra)}]"
        return True


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    handler.addFilter(ContextFilter())
    root = logging.getLogger("sceneworks")
    root.setLevel(level.upper())
    root.addHandler(handler)
    root.propagate = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    context = await build_context(settings)
    app.state.context = context
    yield
    await context.shutdown()


def create_app(settings=None, context=None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(
        title="SceneWorks",
        version="2.5.2",
        lifespan=lifespan,
        description="AI-native software company control plane.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        # The API binds to localhost only, so any local page is a trusted
        # caller regardless of which port the dev server picked. Without this,
        # `next dev` falling back to 3001 (port busy) or the user opening
        # http://127.0.0.1:3000 made every browser request fail CORS and
        # surface as "TypeError: Failed to fetch".
        allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID", "X-Process-Time-Ms"],
    )

    @app.middleware("http")
    async def request_diagnostics(request: Request, call_next):
        """Attach low-noise correlation/timing headers to API responses.

        The browser API client sends an X-Request-ID and records the matching
        client duration. Keeping this as response metadata makes slow paths
        diagnosable in development without adding per-request production log
        noise or buffering SSE responses.
        """
        correlation_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        started = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - started) * 1000
        response.headers["X-Request-ID"] = correlation_id
        response.headers["X-Process-Time-Ms"] = f"{elapsed_ms:.1f}"
        return response
    if context is not None:
        # Test helper: context supplied directly, lifespan skipped.
        app.state.context = context
    app.include_router(projects_router)
    app.include_router(tasks_router)
    app.include_router(executions_router)
    app.include_router(company_router)
    app.include_router(backends_router)
    app.include_router(roles_router)
    app.include_router(settings_router)
    app.include_router(events_router)
    app.include_router(dashboard_router)
    app.include_router(memory_router)

    @app.get("/api/health")
    async def health() -> dict:
        return {
            "status": "ok",
            "app": settings.app_name,
            "active_executions": len(app.state.context.execution_engine.active_ids()),
        }

    return app


app = create_app()


def main() -> None:
    """Run the API with the port from settings.

    `uv run uvicorn app.main:app` ignores the configured port and binds
    uvicorn's default 8000, while the frontend targets 8010 — the classic
    "TypeError: Failed to fetch" footgun. Running the module (`uv run python
    -m app.main`) makes the server and the web client agree by construction.
    """
    import uvicorn

    settings = get_settings()
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=True)


if __name__ == "__main__":
    main()
