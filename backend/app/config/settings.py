"""SceneWorks configuration.

All settings are overridable through environment variables (prefix SCENEWORKS_)
or a backend/.env file. No machine-specific paths are hard-coded here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent


class ModelProfileRoute(BaseModel):
    """Concrete execution target for one provider-neutral model profile.

    Both fields are optional: a route may override only the backend and inherit
    that backend's configured default model, or override only the model while
    keeping the role's backend. No provider/model identifiers are hard-coded by
    SceneWorks.
    """

    backend: str | None = None
    model: str | None = None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SCENEWORKS_",
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "SceneWorks"
    host: str = "127.0.0.1"
    port: int = 8010

    database_url: str = "sqlite+aiosqlite:///./data/sceneworks.db"

    # Root directory for isolated agent/work sessions. Must not be inside a
    # managed repository. Relative paths resolve against the backend cwd.
    worktree_root: Path = Path("data/worktrees")

    # SceneWorks-owned task context and durable GUI evidence. Both live outside
    # managed repositories/worktrees under the same project-scoped storage root
    # so project purge can remove them together without touching user assets.
    attachment_root: Path = Path("data/attachments")
    attachment_max_bytes: int = 20_000_000
    attachment_task_max_bytes: int = 50_000_000
    attachment_task_max_count: int = 8
    mcp_attachment_max_bytes: int = 5_000_000
    gui_screenshot_max_bytes: int = 8_000_000

    # Provider-neutral role intent -> concrete backend/model mapping (WP8).
    model_profile_routes: dict[str, ModelProfileRoute] = Field(default_factory=dict)
    # Optional first edge of the routing chain: role -> provider-neutral profile.
    # Concrete provider model identifiers remain centralized in model_profile_routes.
    role_model_profile_overrides: dict[str, str] = Field(default_factory=dict)

    # Gemini CLI (ACP backend). None -> discover "gemini" on PATH.
    gemini_executable: str | None = None
    gemini_extra_args: list[str] = Field(default_factory=list)
    gemini_model: str | None = None
    gemini_environment: dict[str, str] = Field(default_factory=dict)
    gemini_startup_timeout_seconds: int = 120

    # OpenCode backup backend. This path intentionally uses ``opencode run``
    # rather than ACP, proving that SceneWorks agent routing is transport-neutral.
    # Provider credentials/model catalogs remain owned by OpenCode.
    opencode_executable: str | None = None
    opencode_model: str | None = None
    opencode_agent: str | None = None
    opencode_extra_args: list[str] = Field(default_factory=list)
    opencode_environment: dict[str, str] = Field(default_factory=dict)

    git_timeout_seconds: int = 300
    execution_timeout_seconds: int = 5400
    cancel_grace_seconds: int = 15

    context_max_bytes: int = 200_000
    context_file_max_bytes: int = 60_000
    sse_replay_events: int = 500

    log_level: str = "INFO"
    cors_origins: list[str] = ["http://localhost:3000"]

    # MCP reasoning/control interface.
    # observe  -> semantic read tools only;
    # standard -> governed SceneWorks actions (tasks/roles/workflows);
    # advanced -> standard plus provider-neutral EngineeringSessions exposing
    #             SceneWorks-owned workspace/command/process/Git/PCS/GUI tools.
    mcp_enabled: bool = True
    mcp_mode: Literal["observe", "standard", "advanced"] = "observe"
    mcp_allow_actions: bool = False
    advanced_session_permissions: list[str] = Field(
        default_factory=lambda: [
            "repository_read",
            "repository_write",
            "shell_execute",
            "process_control",
            "git_commit",
            "network_access",
            "agent_delegate",
            "external_asset_read",
            "gui_observe",
            "gui_automate",
            "subagents",
        ]
    )
    mcp_tool_max_chars: int = 120_000

    checkpoint_db_path: str = "data/workflow_checkpoints.db"
    max_review_iterations: int = 3

    roles_dir: Path = BACKEND_DIR / "app" / "roles" / "prompts"

    # OpenHands backend.
    openhands_url: str | None = None
    openhands_base_url: str | None = None
    openhands_executable: str | None = None
    openhands_model: str | None = None
    openhands_api_key: str | None = None
    openhands_mode: str | None = None
    openhands_max_iterations: int = 40
    openhands_environment: dict[str, str] = Field(default_factory=dict)

    default_backend: Literal["gemini_acp", "opencode", "openhands", "fake"] = "gemini_acp"

    @property
    def effective_mcp_mode(self) -> Literal["observe", "standard", "advanced"]:
        if self.mcp_mode == "observe" and self.mcp_allow_actions:
            return "standard"
        return self.mcp_mode


def resolve_path(path: Path, base: Path | None = None) -> Path:
    """Resolve a possibly-relative configured path against a base directory."""
    if path.is_absolute():
        return path
    return (base or Path.cwd()) / path


def get_settings() -> Settings:
    return Settings()
