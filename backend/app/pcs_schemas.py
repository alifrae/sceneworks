"""Validated PCS runtime-control contracts (WP16)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


_NAME_PATTERN = r"^[A-Za-z0-9._-]+$"


class PcsPortCheck(BaseModel):
    name: str = Field(default="", max_length=120)
    host: str = Field(default="127.0.0.1", min_length=1, max_length=255)
    port: int = Field(ge=1, le=65535)


class PcsAssetRoot(BaseModel):
    """Explicit host directory made available read-only through an alias."""

    path: str = Field(min_length=1, max_length=1000)
    read_only: bool = True

    @model_validator(mode="after")
    def _read_only_only(self) -> "PcsAssetRoot":
        if not self.read_only:
            raise ValueError("WP16 external asset roots are read-only")
        return self


class PcsRunProfile(BaseModel):
    command: str = Field(min_length=1, max_length=2000)
    args: list[str] = Field(default_factory=list, max_length=100)
    cwd: str = Field(default="", max_length=1000)
    environment: dict[str, str] = Field(default_factory=dict)
    expected_ports: list[PcsPortCheck] = Field(default_factory=list, max_length=32)
    log_paths: list[str] = Field(default_factory=list, max_length=32)
    crash_paths: list[str] = Field(default_factory=list, max_length=32)
    api_base_url: str | None = Field(default=None, max_length=1000)
    health_path: str | None = Field(default=None, max_length=500)
    runtime_state_path: str | None = Field(default=None, max_length=500)
    startup_timeout_seconds: int = Field(default=30, ge=1, le=300)

    @field_validator("cwd", "log_paths", "crash_paths")
    @classmethod
    def _reject_absolute_project_paths(cls, value):
        values = value if isinstance(value, list) else [value]
        for item in values:
            text = str(item or "").strip()
            if not text:
                continue
            if text.startswith(("/", "\\")) or (len(text) > 2 and text[1] == ":"):
                raise ValueError("cwd/log/crash paths must be worktree-relative")
            if ".." in text.replace("\\", "/").split("/"):
                raise ValueError("cwd/log/crash paths may not contain '..'")
        return value


class PcsRunbookStep(BaseModel):
    action: Literal[
        "command",
        "start",
        "stop",
        "restart",
        "health",
        "runtime_state",
    ]
    profile: str | None = Field(default=None, max_length=120, pattern=_NAME_PATTERN)
    command: str | None = Field(default=None, max_length=2000)
    args: list[str] = Field(default_factory=list, max_length=100)
    cwd: str = Field(default="", max_length=1000)
    timeout_seconds: int = Field(default=300, ge=1, le=3600)
    expect_exit_code: int | None = 0

    @model_validator(mode="after")
    def _validate_command_step(self) -> "PcsRunbookStep":
        if self.action == "command" and not (self.command or "").strip():
            raise ValueError("command runbook steps require command")
        return self


class PcsVerificationRunbook(BaseModel):
    description: str = Field(default="", max_length=2000)
    steps: list[PcsRunbookStep] = Field(min_length=1, max_length=50)
    stop_on_failure: bool = True


class PcsRuntimeControlConfig(BaseModel):
    """Project-level PCS control configuration persisted by SceneWorks."""

    default_profile: str | None = Field(default=None, max_length=120, pattern=_NAME_PATTERN)
    profiles: dict[str, PcsRunProfile] = Field(default_factory=dict)
    runbooks: dict[str, PcsVerificationRunbook] = Field(default_factory=dict)
    asset_roots: dict[str, PcsAssetRoot] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_names_and_references(self) -> "PcsRuntimeControlConfig":
        for collection_name, values in (
            ("profile", self.profiles),
            ("runbook", self.runbooks),
            ("asset root", self.asset_roots),
        ):
            if len(values) > 50:
                raise ValueError(f"at most 50 {collection_name}s are allowed")
            for name in values:
                if not name or len(name) > 120:
                    raise ValueError(f"invalid {collection_name} name")
                if not all(char.isalnum() or char in "._-" for char in name):
                    raise ValueError(
                        f"{collection_name} names may contain only letters, numbers, '.', '_' and '-'"
                    )
        if self.default_profile and self.default_profile not in self.profiles:
            raise ValueError("default_profile must name a configured profile")
        for runbook in self.runbooks.values():
            for step in runbook.steps:
                if step.profile and step.profile not in self.profiles:
                    raise ValueError(
                        f"runbook references unknown profile {step.profile!r}"
                    )
        return self
