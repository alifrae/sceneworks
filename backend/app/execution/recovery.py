"""Recovery semantics documentation.

SceneWorks uses LangGraph checkpointing (SQLite) for workflow durability.
Checkpoints are written after each graph node execution. On restart:

Safe-to-resume states:
  - AWAITING_ARCHITECTURE_APPROVAL: Workflow waits for human. Mark as interrupt-waiting.
  - READY_TO_IMPLEMENT: Architecture approved. Auto-resume to engineer.
  - CHANGES_REQUESTED: Repair loop interrupted. Auto-resume to engineer.

Cannot safely resume:
  - ARCHITECTURE_ANALYSIS: Active execution was mid-flight. Task -> FAILED.
  - IMPLEMENTING: Active execution was mid-flight. Task -> FAILED.
  - REVIEWING: Active execution was mid-flight. Task -> FAILED.

Completed side effects never replay:
  - Completed executions are in terminal states and are not re-started.
  - Git commits in the worktree are idempotent (no duplicate commits).

Active executions become explicitly INTERRUPTED:
  - Marked in the database with error "interrupted by restart".
  - Their results are discarded.

No duplicate Engineer/Reviewer executions:
  - The idempotency check in _node_engineer prevents re-execution
    of already-completed executions.
  - New executions are created only for genuinely incomplete work.

Human-action states:
  - If the workflow was at architecture_approval (LangGraph interrupt()),
    the checkpoint is preserved. The graph cannot auto-resume because
    it's waiting for human input. The task remains in AWAITING_ARCHITECTURE_APPROVAL
    and the UI should prompt the human to approve/reject/revision.
"""
