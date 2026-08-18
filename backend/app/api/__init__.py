from app.api.company import router as company_router
from app.api.dashboard import router as dashboard_router
from app.api.events import router as events_router
from app.api.executions import router as executions_router
from app.api.memory import router as memory_router
from app.api.policy import router as policy_router
from app.api.projects import router as projects_router
from app.api.settings import backends_router, roles_router, settings_router
from app.api.tasks import router as tasks_router

__all__ = [
    "company_router",
    "dashboard_router",
    "events_router",
    "executions_router",
    "memory_router",
    "policy_router",
    "projects_router",
    "roles_router",
    "backends_router",
    "settings_router",
    "tasks_router",
]
