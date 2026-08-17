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
    # Timeout for Gemini subprocess startup, `--version` probes and the ACP
    # `initialize` handshake (seconds). Node startup alone measured ~20s on a
    # cold Windows run, and the handshake exceeded 30s whenever two agents
    # started at once, failing executions with "ACP request initialize timed
    # out". Kept generous: this bounds startup only, not the agent's work
    # (that is execution_timeout_seconds).
    gemini_startup_timeout_seconds: int = 120

    # Timeout for a single git command (seconds). Creating a worktree checks
    # out the whole tree, which takes tens of seconds on a typical repository.
    # Previously set to 900 s to paper over fsmonitor daemon accumulation;
    # now that fsmonitor is suppressed per-process, 300 s is generous even
    # for large repositories (~30k files measured ~45 s here).
    git_timeout_seconds: int = 300

    # Hard limit for a single agent execution (seconds).
    # Long enough for an Engineer to iterate on tests and linting inside a
    # real repository. 5400 s (90 min) covers multi-pass tool-calling loops
    # on large codebases.  Keep configurable — a small task should never
    # need this much, and an observer should not assume the UI is frozen.
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

    # LangGraph workflow checkpoint database path (plain file path, not a URL).
    checkpoint_db_path: str = "data/workflow_checkpoints.db"
    # Maximum review-repair iterations before forcing human intervention.
    max_review_iterations: int = 3

    # Path to directory containing editable role prompt files (roles/*.md).
    roles_dir: Path = BACKEND_DIR / "app" / "roles" / "prompts"

    # OpenHands backend.
    # `openhands_url` is the *Agent Server*; `openhands_base_url` is the *LLM*
    # endpoint (any OpenAI-compatible server: LM Studio, vLLM, Ollama). They are
    # different services and conflating them made a local deterministic
    # validation impossible — see docs/backends.md.
    openhands_url: str | None = None
    openhands_base_url: str | None = None
    openhands_executable: str | None = None
    #: litellm-form model id, e.g. "lm_studio/google/gemma-4-e2b" or
    #: "anthropic/claude-sonnet-4-20250514". Required: the SDK rejects an
    #: unspecified model.
    openhands_model: str | None = None
    openhands_api_key: str | None = None
    #: Force a mode instead of resolving one: local | remote | http | cli.
    #: Leave unset for automatic resolution (see OpenHandsBackend.resolve_mode).
    openhands_mode: str | None = None
    #: Upper bound on agent turns per execution. The SDK default is 500, which a
    #: model that never concludes will happily consume — an execution then runs
    #: until the hard timeout with nothing to show. 40 is generous for the
    #: single-task scope SceneWorks gives a role.
    openhands_max_iterations: int = 40
    openhands_environment: dict[str, str] = Field(default_factory=dict)

    # When set to "fake", the default backend is the scripted FakeAgentBackend.
    default_backend: Literal["gemini_acp", "openhands", "fake"] = "gemini_acp"


def resolve_path(path: Path, base: Path | None = None) -> Path:
    """Resolve a possibly-relative configured path against a base directory."""
    if path.is_absolute():
        return path
    return (base or Path.cwd()) / path


def get_settings() -> Settings:
    return Settings()
