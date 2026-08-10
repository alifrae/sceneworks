# PCS Onboarding Example

This guide shows how SceneWorks should be configured for a large existing
repository such as PCS (Point Cloud Studio).

SceneWorks does not add its own code to PCS. Registering PCS stores only
the repository location and metadata. Workflow roles operate on isolated
Git worktrees; only committed snapshot state enters the workflow. The final
agent branch is not merged automatically.

## Example configuration

```text
Project name: PCS
Repository path: <absolute local PCS repository>
Default branch: <detected from repository>
Architecture context:
  docs/architecture.md
  docs/data-pipeline.md
  docs/point-cloud-processing.md
Test commands:
  cargo test --workspace
Build commands:
  cargo build --release
```

## Registration steps

1. Navigate to **Projects** in the SceneWorks web UI
2. Click **Add Repository**
3. Enter the absolute path to your PCS repository (e.g.
   `C:\Users\you\source\pcs` or `/home/you/source/pcs`)
4. Enter friendly name: "PCS"
5. Configure architecture context paths if your project has specific
   architecture documents beyond the defaults (`AGENTS.md`,
   `ARCHITECTURE.md`, `CONTRIBUTING.md`, `ROADMAP.md` which are read
   automatically)
6. Configure test commands specific to your build system (e.g. `cargo test`
   for Rust, `pytest` for Python, `npm test` for Node.js)
7. Click **Register**

## How SceneWorks uses PCS

### Registration
- Stores the repository path, detected default branch, and HEAD commit
- Never modifies any file in PCS
- No SceneWorks code is added to PCS

### Architecture analysis
- The Architect receives a detached read-only worktree at the HEAD commit
- Inspects files via read-only operations
- Receives project context files (AGENTS.md, ARCHITECTURE.md, etc.) read
  from the snapshot, not the mutable human working tree

### Implementation
- The Engineer creates a new branch `sw-task-<id>` in an isolated
  worktree outside the PCS repository
- All file edits, test runs, and commits happen in this worktree
- The original PCS working tree is untouched

### Review
- The Reviewer receives a detached worktree at the Engineer's result commit
- Inspects the diff between the base commit and the result commit
- Runs configured test commands to validate

### Completion
- Task reaches READY_FOR_HUMAN with implementation summary, diff, and
  review verdict
- The human founder reviews and accepts or rejects
- SceneWorks never merges the `sw-task-<id>` branch automatically
- The human decides whether to integrate the changes into PCS

## Uncommitted changes are safe

If you have uncommitted edits in your PCS working tree:
- They are never included in agent worktrees (Git worktrees only include
  committed state)
- They never enter agent prompts (context files are read from the
  worktree snapshot, not the mutable working tree)
- The workflow proceeds on the committed state only

## Best practices

- Commit your work before starting a SceneWorks task to anchor the base
  commit
- Configure task descriptions that are clear and bounded
- Review the architecture analysis before approving
- Inspect the diff and review verdict before accepting
- Decide manually how to integrate completed work into your branch
