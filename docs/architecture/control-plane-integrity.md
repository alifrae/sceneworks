# Runtime and control-plane integrity

This document defines the cross-cutting integrity contract for SceneWorks runtime,
Git project state, semantic MCP reads, PCS configuration, workflow rehydration,
and persisted role artifacts.

## Repository truth

Project registration resolves the supplied repository root to one canonical
absolute path, verifies it through the SceneWorks Git service, and persists that
root. MCP repository observations are always derived from that persisted root.

`sceneworks.get_project_context` reports a repository snapshot containing:

- `is_git`;
- current branch;
- configured/default branch;
- current HEAD commit;
- `clean` / `dirty` when the status probe is available;
- explicit `availability` and typed `diagnostic` data when the registered root
  is missing, unreadable, not Git, or a Git subprocess probe fails.

A probe failure is not converted into a fabricated empty repository. The MCP
response remains semantic and never exposes the registered absolute repository
path.

Project context additionally reports the persisted architecture-context paths,
test commands, build commands, engineering policy and capability profile.
Accepted project memory remains the only authoritative memory set. Memory-load
failure is represented separately from a legitimately empty accepted-memory set.

## Task and diff durability

`sceneworks.get_task` is state-independent: active, completed, failed and
cancelled tasks remain readable after their worktree is removed. If opportunistic
live Git provenance cannot be refreshed, persisted task provenance is still
returned with an explicit degraded availability record.

`sceneworks.get_task_diff` resolves evidence in this order:

1. live task worktree, when it still exists and has a base commit;
2. immutable `base_commit..result_commit` reconstruction in the registered
   repository;
3. persisted `changed_files` provenance as partial evidence;
4. a typed unavailable result such as `base_commit_missing`,
   `result_commit_missing`, or `diff_unavailable`.

A missing/cleaned task worktree is therefore normal lifecycle state, not an MCP
internal error.

## PCS configuration truth

PCS configuration remains owned by the PCS control domain, not by the generic
Project model. `sceneworks.pcs.get_config` distinguishes:

- `availability.state = available`: a persisted PCS configuration exists;
- `availability.state = not_configured`: no PCS configuration has been persisted.

`not_configured` returns `config: null`; SceneWorks does not fabricate a profile.
A deliberately persisted empty profile/runbook/asset-alias collection remains a
valid configured state.

The existing PCS public serializer remains authoritative for redaction. External
asset roots are represented by aliases/configured state rather than absolute
host paths, and secret-bearing environment values are not persisted as ordinary
PCS configuration.

## Restart idempotence

A task already persisted as `AWAITING_ARCHITECTURE_APPROVAL` represents a durable
human wait. Process restart only rehydrates that wait. It does not append another
`workflow.interrupted` event unless a later workflow transition creates a new
wait instance.

`READY_TO_IMPLEMENT` and `CHANGES_REQUESTED` retain their existing controlled
recovery behavior.

## Artifact persistence

Task-less advisory role output is persisted as an `Artifact` keyed by its source
execution. The canonical workflow manager checks for an existing artifact before
handling a repeated execution-finished callback, so restart/callback replay does
not create duplicate immutable role output.

Artifact content remains execution evidence/inference. It is not silently
promoted to accepted Project Memory.

## Test/demo isolation

The automated backend suite uses a per-test temporary SQLite database and
per-test temporary Git repository. The scripted fake backend is available for
deterministic tests, but importing or registering it does not seed tasks into
operator state.

Historical operator data is not hidden or deleted based on names or merely on
use of the fake backend. Any cleanup of legacy demo rows must be an explicit,
provenance-aware operator action rather than heuristic deletion.

## Security invariants

These repairs do not add raw host filesystem, shell, Git or SQL MCP tools. MCP
continues to expose SceneWorks semantics. Internal worktree roots and configured
external host roots remain hidden. Runtime/Git observations captured by
SceneWorks are evidence; provider/agent conclusions remain inference. Accepted
memory remains authoritative and no credentials are introduced into PCS profiles
or ordinary SceneWorks settings.
