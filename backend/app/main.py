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
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import (
    backends_router,
    company_router,
    dashboard_router,
    events_router,
    executions_router,
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
        version="1.0.0",
        lifespan=lifespan,
        description="AI-native software company control plane.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
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

    @app.get("/api/health")
    async def health() -> dict:
        return {
            "status": "ok",
            "app": settings.app_name,
            "active_executions": len(app.state.context.execution_engine.active_ids()),
        }

    return app


app = create_app()
