"""Validated PCS runtime-control contracts (WP16)."""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator, model_validator


_NAME_PATTERN = r"^[A-Za-z0-9._-]+$"
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _validate_relative_path(value: str, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        return value
    if text.startswith(("/", "\\")) or (len(text) > 2 and text[1] == ":"):
        raise ValueError(f"{label} must be worktree-relative")
    if ".." in text.replace("\\", "/").split("/"):
        raise ValueError(f"{label} may not contain '..'")
    return value


class PcsPortCheck(BaseModel):
    name: str = Field(default="", max_length=120)
    host: str = Field(default="127.0.0.1", min_length=1, max_length=255)
    port: int = Field(ge=1, le=65535)

    @field_validator("host")
    @classmethod
    def _loopback_only(cls, value: str) -> str:
        if value.strip().lower() not in _LOOPBACK_HOSTS:
            raise ValueError("WP16 PCS port health checks are loopback-only")
        return value


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

    @field_validator("cwd")
    @classmethod
    def _validate_cwd(cls, value: str) -> str:
        return _validate_relative_path(value, "cwd")

    @field_validator("log_paths", "crash_paths")
    @classmethod
    def _validate_project_paths(cls, value: list[str]) -> list[str]:
        for item in value:
            _validate_relative_path(item, "log/crash path")
        return value

    @field_validator("api_base_url")
    @classmethod
    def _validate_loopback_api(cls, value: str | None) -> str | None:
        if not value:
            return value
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or (parsed.hostname or "").lower() not in _LOOPBACK_HOSTS:
            raise ValueError("PCS api_base_url must target localhost/loopback")
        if parsed.username or parsed.password:
            raise ValueError("PCS api_base_url may not embed credentials")
        return value

    @field_validator("health_path", "runtime_state_path")
    @classmethod
    def _validate_api_paths(cls, value: str | None) -> str | None:
        if not value:
            return value
        parsed = urlparse(value)
        if parsed.scheme or parsed.netloc or value.startswith("//"):
            raise ValueError("PCS API endpoint paths must be relative to api_base_url")
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

    @field_validator("cwd")
    @classmethod
    def _validate_cwd(cls, value: str) -> str:
        return _validate_relative_path(value, "runbook cwd")

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
