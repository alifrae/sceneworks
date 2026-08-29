from app.api.attachments import router as attachments_router
from app.api.company import router as company_router
from app.api.control_center import router as control_center_router
from app.api.dashboard import router as dashboard_router
from app.api.events import router as events_router
from app.api.executions import router as executions_router
from app.api.initiatives import router as initiatives_router
from app.api.mcp import router as mcp_router
from app.api.memory import router as memory_router
from app.api.pcs import router as pcs_router
from app.api.projects import router as projects_router
from app.api.settings import backends_router, roles_router, settings_router
from app.api.tasks import router as tasks_router

__all__ = [
    "attachments_router",
    "company_router",
    "control_center_router",
    "dashboard_router",
    "events_router",
    "executions_router",
    "initiatives_router",
    "mcp_router",
    "memory_router",
    "pcs_router",
    "projects_router",
    "roles_router",
    "backends_router",
    "settings_router",
    "tasks_router",
]
