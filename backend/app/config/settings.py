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

    # Root directory for isolated agent worktrees. Must not be inside a managed
    # repository. Relative paths resolve against the backend working directory.
    worktree_root: Path = Path("data/worktrees")

    # Provider-neutral role intent -> concrete backend/model mapping (WP8).
    # Example environment value:
    # SCENEWORKS_MODEL_PROFILE_ROUTES='{"strongest":{"backend":"gemini_acp","model":"<model>"}}'
    # Empty by default: existing role backend + backend default model semantics
    # remain intact until an operator deliberately configures a route.
    model_profile_routes: dict[str, ModelProfileRoute] = Field(default_factory=dict)

    # Gemini CLI (ACP backend). None -> discover "gemini" on PATH.
    gemini_executable: str | None = None
    gemini_extra_args: list[str] = Field(default_factory=list)
    gemini_model: str | None = None
    gemini_environment: dict[str, str] = Field(default_factory=dict)
    # Timeout for Gemini subprocess startup, `--version` probes and the ACP
    # `initialize` handshake (seconds). Node startup alone measured ~20s on a
    # cold Windows run, and the handshake exceeded 30s whenever two agents
    # started at once, failing executions with "ACP request initialize timed
    # out". Kept generous: this bounds startup only, not the agent's work
    # (that is execution_timeout_seconds).
    gemini_startup_timeout_seconds: int = 120

    # Timeout for a single git command (seconds). Creating a worktree checks
    # out the whole tree, which takes tens of seconds on a typical repository.
    git_timeout_seconds: int = 300

    # Hard limit for a single agent execution (seconds).
    execution_timeout_seconds: int = 5400
    # Grace period after cancellation before the engine force-kills (seconds).
    cancel_grace_seconds: int = 15

    # Prompt/context limits.
    context_max_bytes: int = 200_000
    context_file_max_bytes: int = 60_000

    # Number of events replayed to SSE clients on connect.
    sse_replay_events: int = 500

    log_level: str = "INFO"
    cors_origins: list[str] = ["http://localhost:3000"]

    # WP11 MCP reasoning interface.
    #
    # observe  -> semantic read tools only;
    # standard -> governed SceneWorks action tools (tasks/roles/workflows);
    # advanced -> standard tools plus persistent Gemini ACP execution sessions
    #             supervised iteratively by the external MCP client.
    mcp_enabled: bool = True
    mcp_mode: Literal["observe", "standard", "advanced"] = "observe"
    # Backward-compatible flag used by the first WP11 prototype and existing
    # deployments/tests. If true while mcp_mode is still observe, effective mode
    # is standard. New configuration should use mcp_mode.
    mcp_allow_actions: bool = False
    # Capabilities the operator is willing to grant an Advanced-mode session.
    # Per-session requests can choose a subset, never exceed this allowlist.
    advanced_session_permissions: list[str] = Field(
        default_factory=lambda: [
            "repository_read",
            "repository_write",
            "shell_execute",
            "git_commit",
            "network_access",
            "subagents",
        ]
    )
    # Total text budget returned by one semantic MCP tool call. Large diffs and
    # artifacts are truncated with an explicit marker rather than overflowing
    # an external model's context window.
    mcp_tool_max_chars: int = 120_000

    # LangGraph workflow checkpoint database path (plain file path, not a URL).
    checkpoint_db_path: str = "data/workflow_checkpoints.db"
    # Maximum review-repair iterations before forcing human intervention.
    max_review_iterations: int = 3

    # Path to directory containing editable role prompt files (roles/*.md).
    roles_dir: Path = BACKEND_DIR / "app" / "roles" / "prompts"

    # OpenHands backend.
    openhands_url: str | None = None
    openhands_base_url: str | None = None
    openhands_executable: str | None = None
    #: litellm-form model id, e.g. "lm_studio/google/gemma-4-e2b" or
    #: "anthropic/claude-sonnet-4-20250514". Required: the SDK rejects an
    #: unspecified model.
    openhands_model: str | None = None
    #: LLM/provider API key used by the OpenHands SDK. Never returned by the API.
    openhands_api_key: str | None = None
    #: Force a mode instead of resolving one: local | remote | http | cli.
    openhands_mode: str | None = None
    #: Upper bound on agent turns per execution.
    openhands_max_iterations: int = 40
    openhands_environment: dict[str, str] = Field(default_factory=dict)

    # When set to "fake", the default backend is the scripted FakeAgentBackend.
    default_backend: Literal["gemini_acp", "openhands", "fake"] = "gemini_acp"

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
