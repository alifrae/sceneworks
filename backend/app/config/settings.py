"""SceneWorks configuration.

All settings are overridable through environment variables (prefix SCENEWORKS_)
or a backend/.env file. No machine-specific paths are hard-coded here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent


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

    # Root directory for isolated agent worktrees. Must not be inside a managed
    # repository. Relative paths resolve against the backend working directory.
    worktree_root: Path = Path("data/worktrees")

    # Gemini CLI (ACP backend). None -> discover "gemini" on PATH.
    gemini_executable: str | None = None
    gemini_extra_args: list[str] = Field(default_factory=list)
    gemini_model: str | None = None
    gemini_environment: dict[str, str] = Field(default_factory=dict)
    # Timeout used for Gemini subprocess startup/diagnostics (seconds).
    gemini_startup_timeout_seconds: int = 30

    # Hard limit for a single agent execution (seconds).
    execution_timeout_seconds: int = 1800
    # Grace period after cancellation before the engine force-kills (seconds).
    cancel_grace_seconds: int = 15

    # Prompt/context limits.
    context_max_bytes: int = 200_000
    context_file_max_bytes: int = 60_000

    # Number of events replayed to SSE clients on connect.
    sse_replay_events: int = 500

    log_level: str = "INFO"
    cors_origins: list[str] = ["http://localhost:3000"]

    # Path to directory containing editable role prompt files (roles/*.md).
    roles_dir: Path = BACKEND_DIR / "app" / "roles" / "prompts"

    # When set to "fake", the default backend is the scripted FakeAgentBackend.
    default_backend: Literal["gemini_acp", "fake"] = "gemini_acp"


def resolve_path(path: Path, base: Path | None = None) -> Path:
    """Resolve a possibly-relative configured path against a base directory."""
    if path.is_absolute():
        return path
    return (base or Path.cwd()) / path


def get_settings() -> Settings:
    return Settings()
