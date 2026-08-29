"""PCS control-plane availability helpers."""

from __future__ import annotations

from app.models import Project
from app.pcs_models import PcsProjectControl
from app.pcs_schemas import PcsRuntimeControlConfig
from app.services.pcs_control import PcsControlError, PcsControlService


class IntegrityPcsControlService(PcsControlService):
    """Preserve the distinction between absent and intentionally empty PCS config."""

    async def get_config_state(
        self, project_id: int
    ) -> tuple[bool, PcsRuntimeControlConfig]:
        async with self._session_factory() as session:
            if await session.get(Project, project_id) is None:
                raise PcsControlError(f"project {project_id} not found")
            row = await session.get(PcsProjectControl, project_id)

        if row is None:
            return False, PcsRuntimeControlConfig()
        try:
            return True, PcsRuntimeControlConfig.model_validate(row.config or {})
        except Exception as exc:  # noqa: BLE001 - persisted corruption is a domain error
            raise PcsControlError(
                f"stored PCS runtime configuration is invalid: {exc}"
            ) from exc
