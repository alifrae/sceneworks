"""API schemas (Pydantic v2)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    repository_path: str = Field(min_length=1, max_length=1000)
    default_branch: str | None = None
    architecture_context_paths: list[str] = []
    test_commands: list[str] = []
    build_commands: list[str] = []
    worktree_root_override: str | None = None


class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    default_branch: str | None = None
    architecture_context_paths: list[str] | None = None
    test_commands: list[str] | None = None
    build_commands: list[str] | None = None
    worktree_root_override: str | None = None


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str
    repository_path: str
    default_branch: str
    status: str
    architecture_context_paths: list[Any]
    test_commands: list[Any]
    build_commands: list[Any]
    worktree_root_override: str | None
    created_at: datetime
    updated_at: datetime
    active_task_count: int = 0


class RepoStatusOut(BaseModel):
    is_git: bool
    head_branch: str | None
    head_commit: str | None
    error: str | None = None
    worktrees: list[dict] = []
    active_tasks: int = 0


class TaskCreate(BaseModel):
    project_id: int
    title: str = Field(min_length=1, max_length=300)
    description: str = ""
    priority: Literal["low", "medium", "high"] = "medium"


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    title: str
    description: str
    status: str
    priority: str
    current_role: str | None
    current_execution_id: str | None
    base_commit: str | None
    task_branch: str | None
    worktree_path: str | None
    result_commit: str | None
    architecture_result: str | None
    implementation_summary: str | None
    review_result: str | None
    created_at: datetime
    updated_at: datetime
    project_name: str = ""
    allowed_actions: list[str] = []
    execution_status: str | None = None


class DiffOut(BaseModel):
    stat: str = ""
    full: str = ""
    commits: list[dict] = []
    status: str = ""
    error: str | None = None


class ActionRequest(BaseModel):
    reason: str = ""
    notes: str = ""


class ExecutionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    task_id: int | None
    role: str
    backend: str
    model_profile: str | None
    status: str
    workspace: dict = {}
    prompt_preview: str | None
    result: str | None
    error: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    execution_id: str | None
    task_id: int | None
    type: str
    payload: dict
    severity: str
    timestamp: datetime


class BackendOut(BaseModel):
    key: str
    label: str
    available: bool
    version: str | None
    detail: str | None


class RoleOut(BaseModel):
    key: str
    display_name: str
    description: str
    backend: str
    model_profile: str | None
    permissions: list[str]
    can_modify_source: bool
    can_commit: bool
    responsibilities: list[str]


class CompanyAskRequest(BaseModel):
    role: str
    project_id: int | None = None
    question: str = Field(min_length=1, max_length=5000)


class ArtifactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    role: str
    project_id: int | None
    title: str
    content: str
    source_execution_id: str | None
    created_at: datetime


class SettingsOut(BaseModel):
    worktree_root: str
    gemini_executable: str | None
    gemini_model: str | None
    gemini_extra_args: list[str]
    execution_timeout_seconds: int
    cancel_grace_seconds: int
    default_backend: str
    log_level: str
    context_max_bytes: int
    database_url: str
    backends: list[BackendOut]


class SettingsUpdate(BaseModel):
    worktree_root: str | None = None
    gemini_executable: str | None = None
    gemini_model: str | None = None
    execution_timeout_seconds: int | None = Field(default=None, ge=60, le=86400)


class DashboardOut(BaseModel):
    active_tasks: int
    awaiting_approval: int
    running_executions: int
    recently_completed: list[TaskOut]
    failed_executions: list[ExecutionOut]
    roles: list[dict]


# --- Project Memory schemas (V2.4) ---

VALID_MEMORY_TYPES = {
    "initiative_summary",
    "architecture_decision",
    "product_decision",
    "technology_decision",
    "constraint",
}


class MemoryCreate(BaseModel):
    project_id: int
    type: str
    title: str = Field(min_length=1, max_length=500)
    content: str = Field(min_length=1)
    status: str = "proposed"
    tags: list[str] = []
    source: str | None = None
    source_task_id: int | None = None
    source_execution_id: str | None = None
    source_commit: str | None = None


class MemoryUpdate(BaseModel):
    type: str | None = None
    title: str | None = None
    content: str | None = None
    status: str | None = None
    tags: list[str] | None = None
    source: str | None = None


class MemoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    type: str
    title: str
    content: str
    status: str
    tags: list
    source: str | None
    source_task_id: int | None
    source_execution_id: str | None
    source_commit: str | None
    supersedes_id: int | None
    created_at: datetime
    updated_at: datetime


class MemorySearchParams(BaseModel):
    query: str = ""
    types: list[str] | None = None
    status: str | None = None
    tags: list[str] | None = None
    limit: int = Field(default=20, ge=1, le=100)


class ProjectPolicyIn(BaseModel):
    """PUT body: a full replace of the project's policy (WP4).

    Not a partial update -- a policy is the project's current declared
    contract, so the caller sends the whole thing every time, matching
    ProjectPolicyService.upsert()'s semantics.
    """

    protected_paths: list[str] = []
    go_no_go_commands: list[str] = []
    forbidden_dependency_directions: list[str] = []
    architecture_invariants: list[str] = []
    documentation_requirements: list[str] = []
    performance_constraints: list[str] = []
    required_review_checks: list[str] = []
    release_requirements: list[str] = []
    policy_file_paths: list[str] = []


class ProjectPolicyOut(BaseModel):
    """`id`/`created_at`/`updated_at` are None for a project with no policy
    configured yet -- ProjectPolicyService.get_or_default() returns an unsaved
    row with empty lists rather than 404ing, matching how Project.test_commands
    is always present and defaults to []. None means "never saved", not
    "unknown"."""

    model_config = ConfigDict(from_attributes=True)

    id: int | None
    project_id: int
    protected_paths: list[Any]
    go_no_go_commands: list[Any]
    forbidden_dependency_directions: list[Any]
    architecture_invariants: list[Any]
    documentation_requirements: list[Any]
    performance_constraints: list[Any]
    required_review_checks: list[Any]
    release_requirements: list[Any]
    policy_file_paths: list[Any]
    created_at: datetime | None
    updated_at: datetime | None
