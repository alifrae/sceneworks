# WP7 — WorkflowManager decomposition

## Why this work exists

The WP0 audit found that `backend/app/workflows/manager.py` owned too many
unrelated responsibilities: LangGraph topology/routing, node bodies, execution
preparation, persistence, worktree lifecycle, result capture, recovery, memory
injection, event publication, and task-less company artifact handling.

WP7 fixes responsibility sprawl, not merely file size.

## Preserved invariants

WP7 is a behavioral refactor. These contracts remain unchanged:

- `WorkflowManager` remains the public API used by FastAPI/evaluation/tests.
- LangGraph remains the sole task-workflow orchestrator.
- Human architecture approval remains mandatory before implementation.
- One pinned repository commit is reused across the workflow.
- Agent roles operate in isolated worktrees.
- Reviewer repair-loop and restart semantics remain unchanged.
- ExecutionEngine/agent backends do not depend on LangGraph.
- Deterministic qualification is the merge barrier.

## Final decomposition

```text
app.workflows.WorkflowManager
        |
        v
orchestrator.py     public composition facade / stable API
   |
   +--> graph_core.py         LangGraph topology, routing, checkpoints, thin nodes
   +--> runtime.py            persistence, events, execution bridge, memory
   +--> advisory_runtime.py   triage + Product/CTO/Technical Expert execution
   +--> role_runtime.py       Architect/Engineer/Reviewer execution lifecycle
   +--> control.py            founder commands + graph scheduling
   +--> recovery.py           restart reconciliation policy

manager.py          backward-compatible re-export only
```

### `graph_core.py`

The only workflow module that owns graph topology. It contains:

- checkpoint connection/lifecycle
- graph construction and conditional edges
- thin node coordination
- routing decisions
- graph invocation/error containment

It does not create executions, prepare worktrees/prompts, persist task results,
retrieve memory, or own restart policy.

### `runtime.py`

Framework-neutral workflow infrastructure:

- task/project lookup
- validated task-state transitions
- workflow event persistence/publication
- execution row creation and completion signalling
- memory retrieval/injection evidence
- task notes
- task-less company artifact capture and ask-worktree cleanup

It intentionally imports no LangGraph package.

### `advisory_runtime.py`

Owns operational mechanics for Triage and optional Product/CTO/Technical Expert
runs: pinned snapshots, prompts, execution lifecycle, degraded-triage evidence,
and disposable advisory worktree cleanup.

### `role_runtime.py`

Owns Architect/Engineer/Reviewer mechanics: worktree preparation, prompts,
execution creation, implementation commit capture, reviewer diff context,
result persistence, approval cleanup and review-worktree cleanup.

### `control.py`

Owns founder/API commands: start/resume, implementation/review triggers,
accept/reject/send-back, cancel/retry, worktree cleanup, and active graph
scheduling/waiting.

### `recovery.py`

Owns restart policy for checkpointed workflows. Human-waiting states remain
paused; resumable implementation/repair states are restarted deliberately.

### `orchestrator.py`

Composes the graph core and operational components while preserving the existing
`WorkflowManager` method contract.

### `manager.py`

A small compatibility shim for older imports. A regression test keeps it below
50 source lines so the monolith cannot silently grow back.

## Verification

`tests/test_workflow_decomposition.py` locks the architectural boundaries and the
existing higher-value barriers verify behavior:

- full provider-independent qualification scenarios
- workflow graph and recovery tests
- API/state-machine tests
- non-live backend suite
- frontend production build

WP7 is complete only when the final compatibility-cleanup head passes all of
those barriers. No architectural inspection alone counts as completion.
