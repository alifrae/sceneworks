"""Recovery semantics.

Documentation module: no code. The behaviour described here lives in
`ExecutionEngine.recover_interrupted()`, `ExecutionEngine.shutdown()` and
`WorkflowManager.recover_workflows()`, and is exercised by the WP1
`restart-recovery` qualification scenario plus `tests/test_engine.py`.

SceneWorks uses LangGraph checkpointing (SQLite) for workflow durability.
Checkpoints are written after each graph node execution.

Shutdown ordering
-----------------
`WorkflowManager.shutdown()` cancels in-flight graph tasks *before* closing the
checkpointer connection. Closing first left graphs writing to a closed aiosqlite
handle, surfacing as `ValueError: no active connection` from inside LangGraph —
an opaque error that told an operator nothing about their task. (Found by the WP1
qualification suite.)

`ExecutionEngine.shutdown()` sets a shutting-down flag so an execution cancelled
by the teardown is recorded as **INTERRUPTED**, not CANCELLED. The distinction
matters twice: an operator can tell "somebody cancelled this" from "the process
stopped underneath it", and restart reconciliation can find the work again.

Restart reconciliation
----------------------
`recover_interrupted()` runs two passes, because either situation occurs alone:

1. **Executions still marked active** (QUEUED/STARTING/RUNNING) — the process
   died without unwinding. They become INTERRUPTED and their results discarded.
2. **Tasks left in a running state** (ARCHITECTURE_ANALYSIS, IMPLEMENTING,
   REVIEWING) with no execution still active. Nothing is running and no graph
   survived, so the state is a false claim of progress. They become FAILED,
   which is the state `retry` accepts.

Pass 2 exists because a *clean* shutdown finalizes its own executions, so pass 1
finds nothing. Without it a task stayed in ARCHITECTURE_ANALYSIS forever: no
agent, no graph, no retry path, and the UI showing it as permanently working.

Safe-to-resume states
---------------------
- `AWAITING_ARCHITECTURE_APPROVAL` — the graph is parked on a LangGraph
  `interrupt()`. The checkpoint is preserved; it cannot auto-resume because it is
  waiting for a human. A `workflow.interrupted` event is emitted so the UI
  prompts for approve/reject/revision.
- `READY_TO_IMPLEMENT` — architecture approved. Auto-resumes at the engineer.
- `CHANGES_REQUESTED` — repair loop interrupted. Auto-resumes at the engineer.

Cannot resume
-------------
- `ARCHITECTURE_ANALYSIS`, `IMPLEMENTING`, `REVIEWING` — an execution was
  mid-flight. The external agent process is gone and its runtime state with it,
  so there is nothing to continue. The task becomes FAILED and is retryable.

What survives, explicitly
-------------------------
A restart must leave no ambiguity about project state. After reconciliation the
following are true and observable:

- committed work survives: `task.result_commit` and the task branch are
  untouched by recovery;
- the engineer worktree remains on disk if it existed, and is reclaimed lazily
  when the next role needs that path (`GitWorktreeService._claim_destination`);
  registered worktrees are never overwritten;
- interrupted executions carry status INTERRUPTED and an error explaining why;
- every task is in a state that either waits for a human or accepts `retry`.

Completed side effects never replay
-----------------------------------
- Completed executions are terminal and are not re-started.
- `_finish_architect` / `_finish_engineer` / `_finish_reviewer` apply their state
  transition only from the state that transition is valid in, so re-entering a
  node after a restart refreshes the stored result and is otherwise a no-op.
  (Before V3.0 they transitioned unconditionally, so re-entry raised
  InvalidTransition and failed the task — the opposite of what the idempotency
  check existed to achieve.)
- A `git.commit` event is emitted only when the Engineer produced a commit
  distinct from the base, so a replayed finish does not announce a second commit.

Not recovered
-------------
- The in-process asyncio graph task does not survive a restart. Workflows resume
  by re-invoking the compiled graph from its checkpoint, which is why only the
  states listed above can auto-resume.
- Worktrees are not reconciled eagerly at startup; see above for the lazy path.
"""
