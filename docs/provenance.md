# Git provenance (WP6)

SceneWorks treats Git and persisted workflow records as evidence. Agent prose is never used to decide which files a task changed.

## Persisted task evidence

Each task can expose:

- `base_commit` — repository snapshot pinned for the workflow;
- `result_commit` — implementation commit produced by the task;
- `task_branch` — isolated implementation branch;
- `changed_files` — normalized repo-relative paths observed by `git diff --name-only base_commit..HEAD`;
- accepted project-memory IDs whose source task is this task.

`changed_files` is persisted on the task so the answer remains available after a worktree is cleaned up.

## Capture points

SceneWorks captures changed-file provenance from the task worktree before human terminal actions that may lead to cleanup (`accept`, `reject`, and explicit `cleanup-worktree`). A task-provenance query also performs an opportunistic capture while the worktree still exists.

This is intentionally Git-derived: an Engineer saying “I only changed app.py” cannot override what Git reports.

## Query API

`GET /api/tasks/{task_id}/provenance` returns one task's evidence.

`GET /api/projects/{project_id}/provenance` returns recent task provenance for a project.

`GET /api/projects/{project_id}/provenance?path=backend/app/config/settings.py` answers which persisted tasks touched that exact normalized path.

The path lookup is performed over persisted evidence rather than rerunning Git across old worktrees, so it continues to work after cleanup.

## Example

```json
{
  "task_id": 42,
  "project_id": 3,
  "title": "Fix configuration cache",
  "status": "ACCEPTED",
  "base_commit": "a1b2c3...",
  "result_commit": "d4e5f6...",
  "task_branch": "sw-task-42",
  "changed_files": [
    "backend/app/config/cache.py",
    "backend/tests/test_config_cache.py"
  ],
  "source_memory_ids": [18]
}
```

## Current boundary

This WP6 slice makes change provenance durable and queryable, but it is not yet a full semantic lineage graph. It does not currently answer symbol-level questions such as “which task changed this function,” nor does it reconstruct dependency causality between tasks. It also relies on a surviving task worktree for the first capture; terminal actions capture before cleanup, but a catastrophic loss of the worktree before any capture leaves `changed_files` empty rather than fabricating evidence.

A later hardening step can move the same Git-derived snapshot into the Engineer completion hook once the large workflow manager is decomposed behind the existing qualification barrier.
