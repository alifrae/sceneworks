# WP7 — WorkflowManager decomposition

## Why this work exists

The WP0 audit found that `backend/app/workflows/manager.py` owned too many
unrelated responsibilities: LangGraph topology/routing, node bodies, execution
preparation, persistence, worktree lifecycle, result capture, recovery, memory
injection, event publication, and task-less company artifact handling.

The risk is architectural rather than cosmetic. A change to persistence or
recovery should not require editing the same class that defines graph routing,
and provider-independent runtime infrastructure should not be coupled to
LangGraph.

## Invariants

WP7 is a behavioral refactor. These contracts do not change:

- `WorkflowManager` remains the public API used by FastAPI and tests.
- LangGraph remains the sole task-workflow orchestrator.
- Human architecture approval remains mandatory before implementation.
- The workflow remains pinned to one repository commit.
- Agent roles continue to operate only in isolated worktrees.
- Reviewer repair-loop and restart semantics remain unchanged.
- ExecutionEngine and agent backends do not depend on LangGraph.
- The deterministic qualification suite is the merge barrier.

## Current decomposition

```text
app.workflows.WorkflowManager
        |
        v
orchestrator.py  -- public compatibility facade
   |       |       |
   |       |       +--> recovery.py   restart reconciliation policy
   |       +----------> control.py    founder commands + graph scheduling
   +------------------> runtime.py    persistence/events/execution bridge/memory
        |
        v
manager.py       -- graph topology/nodes during migration
```

### `runtime.py`

Framework-neutral workflow infrastructure:

- task/project lookup
- validated task-state transitions
- workflow event persistence/publication
- execution row creation
- execution completion signalling
- memory retrieval/injection evidence
- task notes
- task-less company artifact capture and ask-worktree cleanup

It intentionally imports no LangGraph package.

### `control.py`

Owns commands exposed by the application/API:

- start workflow
- resume architecture approval
- start implementation/review
- accept/reject/send back
- cancel/retry
- worktree cleanup
- active graph scheduling/waiting

The controller may use LangGraph `Command`, but it does not own node topology or
role execution details.

### `recovery.py`

Owns restart policy for checkpointed workflows. It decides which waiting states
remain paused and which resumable states are restarted.

### `orchestrator.py`

Preserves the existing `WorkflowManager` contract while delegating the concerns
above. The composition root imports `WorkflowManager` from `app.workflows`, not
from the graph implementation module.

## Migration sequence

1. **Runtime/control/recovery boundaries** — implemented in the first WP7 slice.
2. **Role execution lifecycle** — move architect/engineer/reviewer preparation,
   result capture, commit capture, approval cleanup, and disposable worktree
   handling behind a role-runtime component.
3. **Triage/advisory execution** — move repository snapshot preparation and
   advisory-role execution out of graph node methods so nodes become thin
   orchestration adapters.
4. **Graph core** — leave only topology, routing, checkpoint management and thin
   node coordination in the graph implementation.
5. **Compatibility cleanup** — reduce `manager.py` to a compatibility export or
   remove it once internal imports have migrated.

WP7 is complete only after the old responsibility-sprawl implementation is no
longer the runtime owner and the qualification/recovery suites remain green.

## Verification

The first slice adds `tests/test_workflow_decomposition.py` and reuses the
existing higher-value barriers:

- full provider-independent qualification scenarios
- workflow graph tests
- recovery tests
- API/state-machine tests
- non-live backend suite
- frontend production build

No WP7 change is accepted on architectural inspection alone.
