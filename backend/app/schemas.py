"""API schemas (Pydantic v2)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

InitiativeStatus = Literal["planned", "active", "blocked", "completed", "cancelled"]
WorkPackageStatus = Literal["planned", "ready", "active", "blocked", "completed", "cancelled"]
WorkItemType = Literal["task", "bug", "feature", "idea"]
ExecutionMode = Literal["auto", "change", "investigate", "plan", "ask"]


class RoleCapabilityOverlay(BaseModel):
    """Skills, domains, and optional methods active for one role scope."""

    skills: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    methods: list[str] = Field(default_factory=list)


class CapabilityProfile(RoleCapabilityOverlay):
    """Project/task capability overlay with optional per-role specialization."""

    roles: dict[str, RoleCapabilityOverlay] = Field(default_factory=dict)


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    repository_path: str = Field(min_length=1, max_length=1000)
    default_branch: str | None = None
    architecture_context_paths: list[str] = []
    test_commands: list[str] = []
    build_commands: list[str] = []
    capability_profile: CapabilityProfile = Field(default_factory=CapabilityProfile)
    worktree_root_override: str | None = None


class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    default_branch: str | None = None
    architecture_context_paths: list[str] | None = None
    test_commands: list[str] | None = None
    build_commands: list[str] | None = None
    capability_profile: CapabilityProfile | None = None
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
    capability_profile: dict[str, Any] = {}
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


class InitiativeCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    objective: str = ""
    description: str = ""


class InitiativeUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    objective: str | None = None
    description: str | None = None
    status: InitiativeStatus | None = None


class InitiativeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    title: str
    objective: str
    description: str
    status: str
    created_at: datetime
    updated_at: datetime
    work_package_count: int = 0
    completed_work_packages: int = 0
    task_count: int = 0


class WorkPackageCreate(BaseModel):
    key: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9._-]+$")
    title: str = Field(min_length=1, max_length=300)
    description: str = ""
    sequence: int | None = Field(default=None, ge=0)
    depends_on: list[int] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)


class WorkPackageUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = None
    status: WorkPackageStatus | None = None
    sequence: int | None = Field(default=None, ge=0)
    depends_on: list[int] | None = None
    acceptance_criteria: list[str] | None = None


class WorkPackageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    initiative_id: int
    key: str
    title: str
    description: str
    status: str
    sequence: int
    depends_on: list[int]
    acceptance_criteria: list[str]
    created_at: datetime
    updated_at: datetime
    task_count: int = 0


class EngineeringContract(BaseModel):
    """Structured, checkable obligations for one engineering task (WP4)."""

    required_behavior: list[str] = Field(default_factory=list)
    allowed_scope: list[str] = Field(default_factory=list)
    forbidden_changes: list[str] = Field(default_factory=list)
    architecture_constraints: list[str] = Field(default_factory=list)
    required_tests: list[str] = Field(default_factory=list)
    performance_requirements: list[str] = Field(default_factory=list)
    compatibility_requirements: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)


class TaskCreate(BaseModel):
    project_id: int
    work_package_id: int | None = None
    title: str = Field(min_length=1, max_length=300)
    description: str = ""
    priority: Literal["low", "medium", "high"] = "medium"
    work_item_type: WorkItemType = "task"
    requested_mode: ExecutionMode = "auto"
    engineering_contract: EngineeringContract = Field(default_factory=EngineeringContract)
    capability_requirements: CapabilityProfile = Field(default_factory=CapabilityProfile)


class TaskBacklogUpdate(BaseModel):
    """Editable metadata for a task that has not started execution yet."""

    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = None
    priority: Literal["low", "medium", "high"] | None = None
    work_item_type: WorkItemType | None = None
    requested_mode: ExecutionMode | None = None
    work_package_id: int | None = None


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    work_package_id: int | None
    title: str
    description: str
    status: str
    priority: str
    work_item_type: str
    requested_mode: str
    resolved_mode: str | None
    current_role: str | None
    current_execution_id: str | None
    base_commit: str | None
    task_branch: str | None
    worktree_path: str | None
    result_commit: str | None
    architecture_result: str | None
    implementation_summary: str | None
    review_result: str | None
    engineering_contract: dict[str, Any] = {}
    capability_requirements: dict[str, Any] = {}
    advisory_results: dict[str, Any] = {}
    changed_files: list[str] = []
    created_at: datetime
    updated_at: datetime
    project_name: str = ""
    allowed_actions: list[str] = []
    execution_status: str | None = None


class TaskProvenanceOut(BaseModel):
    task_id: int
    project_id: int
    title: str
    status: str
    base_commit: str | None
    result_commit: str | None
    task_branch: str | None
    changed_files: list[str]
    source_memory_ids: list[int] = []


class ProjectProvenanceOut(BaseModel):
    project_id: int
    path: str | None = None
    tasks: list[TaskProvenanceOut] = []


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
    model_name: str | None
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
    persona: str = ""
    core_capabilities: list[str] = []


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
    model_profile_routes: dict[str, dict[str, str | None]]
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
    model_profile_routes: dict[str, dict[str, str | None]] | None = None
    execution_timeout_seconds: int | None = Field(default=None, ge=60, le=86400)


class DashboardOut(BaseModel):
    active_tasks: int
    awaiting_approval: int
    running_executions: int
    recently_completed: list[TaskOut]
    failed_executions: list[ExecutionOut]
    roles: list[dict]


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
