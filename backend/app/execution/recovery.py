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
  - The _finish_architect/_finish_engineer/_finish_reviewer hooks apply their
    state transition only from the state that transition is valid in, so
    re-entering a node after a restart refreshes the stored result and is
    otherwise a no-op. (Before V3.0 they transitioned unconditionally, so
    re-entry raised InvalidTransition and failed the task — the opposite of
    what the idempotency check existed to achieve.)
  - A git.commit event is emitted only when the Engineer actually produced a
    commit distinct from the base, so a replayed finish does not announce a
    second commit.

Active executions become explicitly INTERRUPTED:
  - Marked in the database with error "interrupted by restart".
  - Their results are discarded.

No duplicate Engineer/Reviewer executions:
  - The idempotency check in _node_engineer reuses an already-finished
    execution rather than starting a second one.
  - New executions are created only for genuinely incomplete work.

Not recovered:
  - The in-process asyncio graph task does not survive a restart. Workflows
    are resumed by re-invoking the compiled graph from its checkpoint, which
    is why only the states listed above can auto-resume.
  - Worktrees are not reconciled at startup. A worktree left behind by an
    interrupted run is reclaimed lazily, when the next role needs that path
    (see GitWorktreeService._claim_destination); registered worktrees are
    never overwritten.

Human-action states:
  - If the workflow was at architecture_approval (LangGraph interrupt()),
    the checkpoint is preserved. The graph cannot auto-resume because
    it's waiting for human input. The task remains in AWAITING_ARCHITECTURE_APPROVAL
    and the UI should prompt the human to approve/reject/revision.
"""
