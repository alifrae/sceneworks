"""SceneWorks API entry point.

Trust assumptions:
- The API binds to localhost by default; it is a trusted control plane, not
  a public service.
- Agents run on the same machine as the API, inside isolated Git worktrees.
- There are no user accounts or RBAC: anyone who can reach the API can
  approve tasks. Do not expose it beyond localhost.
- Repositories registered by the user are trusted inputs; agent output is
  always reviewable before integration (SceneWorks never merges).
- The MCP endpoint shares this trust boundary. Use a trusted tunnel or an
  authenticated reverse proxy; do not publish the bare FastAPI service.
"""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.requests import Request

from app import __version__
from app.api import (
    attachments_router,
    backends_router,
    company_router,
    control_center_router,
    dashboard_router,
    events_router,
    executions_router,
    initiatives_router,
    mcp_router,
    memory_router,
    pcs_router,
    projects_router,
    roles_router,
    settings_router,
    tasks_router,
    verification_router,
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
        version=__version__,
        lifespan=lifespan,
        description="Local engineering control, execution and evidence.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID", "X-Process-Time-Ms"],
    )

    @app.middleware("http")
    async def request_diagnostics(request: Request, call_next):
        correlation_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        started = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - started) * 1000
        response.headers["X-Request-ID"] = correlation_id
        response.headers["X-Process-Time-Ms"] = f"{elapsed_ms:.1f}"
        return response

    if context is not None:
        app.state.context = context
    app.include_router(projects_router)
    app.include_router(pcs_router)
    app.include_router(control_center_router)
    app.include_router(initiatives_router)
    app.include_router(tasks_router)
    app.include_router(verification_router)
    app.include_router(attachments_router)
    app.include_router(executions_router)
    app.include_router(company_router)
    app.include_router(backends_router)
    app.include_router(roles_router)
    app.include_router(settings_router)
    app.include_router(events_router)
    app.include_router(dashboard_router)
    app.include_router(memory_router)
    app.include_router(mcp_router)

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

    Running the module keeps the configured backend port and the web client in
    agreement; invoking uvicorn without a port would otherwise use 8000.
    """
    import uvicorn

    settings = get_settings()
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=True)


if __name__ == "__main__":
    main()
