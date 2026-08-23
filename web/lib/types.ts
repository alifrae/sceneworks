// SceneWorks backend types (mirror of backend/app/schemas.py).

export interface Project {
  id: number;
  name: string;
  description: string;
  repository_path: string;
  default_branch: string;
  status: string;
  architecture_context_paths: string[];
  test_commands: string[];
  build_commands: string[];
  worktree_root_override: string | null;
  created_at: string;
  updated_at: string;
  active_task_count: number;
}

export interface RepoStatus {
  is_git: boolean;
  head_branch: string | null;
  head_commit: string | null;
  error: string | null;
  worktrees: { path: string; branch?: string; head?: string }[];
  active_tasks: number;
}

export interface Initiative {
  id: number;
  project_id: number;
  title: string;
  objective: string;
  description: string;
  status: string;
  created_at: string;
  updated_at: string;
  work_package_count: number;
  completed_work_packages: number;
  task_count: number;
}

export interface WorkPackage {
  id: number;
  initiative_id: number;
  key: string;
  title: string;
  description: string;
  status: string;
  sequence: number;
  depends_on: number[];
  acceptance_criteria: string[];
  created_at: string;
  updated_at: string;
  task_count: number;
}

export interface EngineeringContract {
  required_behavior: string[];
  allowed_scope: string[];
  forbidden_changes: string[];
  architecture_constraints: string[];
  required_tests: string[];
  performance_requirements: string[];
  compatibility_requirements: string[];
  acceptance_criteria: string[];
}

export interface Task {
  id: number;
  project_id: number;
  work_package_id: number | null;
  title: string;
  description: string;
  status: string;
  priority: string;
  current_role: string | null;
  current_execution_id: string | null;
  base_commit: string | null;
  task_branch: string | null;
  worktree_path: string | null;
  result_commit: string | null;
  architecture_result: string | null;
  implementation_summary: string | null;
  review_result: string | null;
  engineering_contract: EngineeringContract;
  changed_files: string[];
  created_at: string;
  updated_at: string;
  project_name: string;
  allowed_actions: string[];
  execution_status: string | null;
}

export interface TaskProvenance {
  task_id: number;
  project_id: number;
  title: string;
  status: string;
  base_commit: string | null;
  result_commit: string | null;
  task_branch: string | null;
  changed_files: string[];
  source_memory_ids: number[];
}

export interface ProjectProvenance {
  project_id: number;
  path: string | null;
  tasks: TaskProvenance[];
}

export interface Execution {
  id: string;
  task_id: number | null;
  role: string;
  backend: string;
  model_profile: string | null;
  model_name: string | null;
  status: string;
  workspace: Record<string, unknown>;
  prompt_preview: string | null;
  result: string | null;
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
}

export interface AppEvent {
  id: number;
  execution_id: string | null;
  task_id: number | null;
  type: string;
  payload: Record<string, unknown>;
  severity: string;
  timestamp: string;
}

export interface Backend {
  key: string;
  label: string;
  available: boolean;
  version: string | null;
  detail: string | null;
}

export interface Role {
  key: string;
  display_name: string;
  description: string;
  backend: string;
  model_profile: string | null;
  permissions: string[];
  can_modify_source: boolean;
  can_commit: boolean;
  responsibilities: string[];
}

export interface Artifact {
  id: number;
  kind: string;
  role: string;
  project_id: number | null;
  title: string;
  content: string;
  source_execution_id: string | null;
  created_at: string;
}

export interface ModelProfileRoute {
  backend: string | null;
  model: string | null;
}

export interface Settings {
  worktree_root: string;
  gemini_executable: string | null;
  gemini_model: string | null;
  gemini_extra_args: string[];
  model_profile_routes: Record<string, ModelProfileRoute>;
  execution_timeout_seconds: number;
  cancel_grace_seconds: number;
  default_backend: string;
  log_level: string;
  context_max_bytes: number;
  database_url: string;
  backends: Backend[];
}

export interface Dashboard {
  active_tasks: number;
  awaiting_approval: number;
  running_executions: number;
  recently_completed: Task[];
  failed_executions: Execution[];
  roles: { key: string; display_name: string; backend: string }[];
}

export interface Diff {
  stat: string;
  full: string;
  commits: { sha: string; subject: string; author: string }[];
  status: string;
  error: string | null;
}

export interface ProjectMemory {
  id: number;
  project_id: number;
  type: string;
  title: string;
  content: string;
  status: string;
  tags: string[];
  source: string | null;
  source_task_id: number | null;
  source_execution_id: string | null;
  source_commit: string | null;
  supersedes_id: number | null;
  created_at: string;
  updated_at: string;
}
