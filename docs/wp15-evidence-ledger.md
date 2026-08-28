# WP15 — Engineering Evidence Ledger and Task Correlation

## Status

WP15 extends the provider-neutral Advanced control introduced by WP14 with a
durable evidence model. The goal is not to make ChatGPT or an agent more
trusted; it is to make their engineering decisions independently verifiable.

The standing authority model is:

```text
ChatGPT                 reasoning / supervision
SceneWorks              control plane + execution runtime + evidence authority
Gemini/OpenCode/etc.    optional delegated workers
Git/runtime/process     objective engineering state captured by SceneWorks
```

A provider statement such as "the bug is fixed" is inference. SceneWorks-captured
Git state, file hashes, command results, process output and runtime observations
are evidence used to evaluate that statement.

## Causal model

WP15 makes the Advanced-control chain explicit:

```text
Task (optional governed work item)
  |
EngineeringSession
  |
EngineeringTurn
  |
Action / action_id
  +-- file read/write/search/list
  +-- command
  +-- process lifecycle/output
  +-- Git status/diff/commit
  +-- delegated AgentBackend execution
```

### EngineeringSession -> Task

`sceneworks.engineering_session.create` accepts optional `task_id`.

When present:

- the Task must exist;
- it must belong to the same project;
- the binding is persisted on the EngineeringSession;
- every EngineeringTurn and EngineeringEvidence row snapshots that Task id;
- `sceneworks.get_task` returns its linked EngineeringSessions and evidence
  summaries.

The binding is intentionally optional because project-level investigation and
maintenance remain valid Advanced use cases.

### EngineeringTurn

A turn represents one explicit supervisor iteration, for example:

```text
reproduce failure
investigate root cause
implement bounded fix
verify fix
```

Only one turn may be ACTIVE in an EngineeringSession at a time.

Tools:

```text
sceneworks.engineering_session.begin_turn
sceneworks.engineering_session.finish_turn
```

Direct Advanced actions accept optional `turn_id`. A supplied turn must belong
to the same session and still be ACTIVE.

### Action id

Every instrumented Advanced operation gets a server-generated immutable
`action_id`. The tool result returns it as `evidence_action_id`.

For delegated agent work, the originating `turn_id` and `action_id` are also
written into the persisted Execution workspace snapshot. This connects the
provider execution trace back to the supervisor iteration that caused it.

## Evidence model

`engineering_evidence` is append-only engineering observation data for the
session. Each row stores:

- `engineering_session_id`;
- optional bound `task_id`;
- optional `turn_id`;
- globally unique `action_id`;
- category and operation;
- status;
- start and finish timestamps;
- bounded structured payload.

### File evidence

Reads persist:

- relative path;
- SHA-256;
- requested/read line range;
- total line count;
- truncation flag.

The file content returned to the caller is **not copied into the evidence
ledger**.

Writes persist:

- relative path;
- SHA-256 before, when the file existed;
- SHA-256 after;
- resulting byte count;
- whether the file was created.

This lets the supervisor prove exactly which file state was observed and which
state resulted from an edit without using SQLite as a source-code mirror.

### Command evidence

`sceneworks.command.run` persists:

- executable;
- argument vector;
- relative cwd;
- timeout setting;
- start/finish time;
- exit code;
- bounded stdout/stderr;
- timeout/cancellation flags.

A non-zero exit code is evidence status `FAILED`; the MCP call itself still
returns the actual process result rather than hiding it behind a transport
error.

### Process evidence

Process snapshots now expose/persist:

- SceneWorks process id;
- OS PID;
- executable and arguments;
- start and finish timestamp;
- running/exited state;
- exit code;
- bounded incremental stdout/stderr events.

`process.start` is `RUNNING` evidence. `process.output` becomes FAILED when an
observed naturally exited process has a non-zero exit code. Explicit
`process.stop` is recorded as a successful control operation even when the
termination exit code is non-zero.

The NativeRuntime process registry remains in-memory. WP15 persists evidence
*when SceneWorks observes it through the MCP operation*. It does not yet run a
background durable log collector after SceneWorks itself restarts; that belongs
to the PCS runtime/control work following WP15.

### Git evidence

`sceneworks.git.diff` remains the independent source of the real diff.

WP15 additionally computes changed-file evidence for committed, staged,
unstaged and untracked files:

```text
path
exists
bytes
sha256
```

The evidence ledger does not persist the full Git diff again. Instead it stores
stat/status, changed-file hashes, and SHA-256/character counts for committed,
working and staged diff text. This preserves correlation while keeping Git as
the canonical diff truth.

## Delegated agent history

`sceneworks.agent.delegate` remains optional. The delegated worker can be
Gemini ACP, OpenCode, OpenHands or another registered backend.

WP15 records the delegation itself as evidence and correlates the normal
SceneWorks Execution event stream with the originating turn/action.

`sceneworks.engineering_session.events` therefore returns:

- EngineeringTurns;
- direct EngineeringEvidence actions;
- persisted SceneWorks events from delegated executions, carrying the matching
  turn/action identifiers.

Provider events are useful execution trace, but their natural-language claims
remain inference. A provider-emitted statement does not acquire evidence
authority merely because SceneWorks persisted the event.

## Retrieval surface

Advanced mode adds:

```text
sceneworks.engineering_session.begin_turn
sceneworks.engineering_session.finish_turn
sceneworks.engineering_session.evidence
sceneworks.engineering_session.events
sceneworks.engineering_session.summary
```

### evidence

Paged by monotonic evidence row id through `after_id` and `next_after_id`.
Supports optional turn/category filtering.

### events

Returns the turn list, evidence stream, cursor, and correlated delegated-agent
Execution events.

### summary

High-signal summary designed for verification without relying on an agent to
summarize itself. It includes:

- project/task/session/base-commit/branch correlation;
- task title/status;
- task required tests and acceptance criteria;
- turn/evidence counts;
- evidence category/status counts;
- latest evidence cursor;
- latest actions/failures;
- latest changed-file hashes when Git evidence exists.

`sceneworks.get_task` also includes linked EngineeringSessions plus these
summaries, so Advanced control is not a disconnected side channel from the
governed Task model.

## Intended iterative workflow

```text
create/bind EngineeringSession to Task
        |
begin turn: reproduce
        |
run PCS/test/process actions
        |
inspect evidence
        |
finish turn
        |
begin turn: investigate
        |
direct inspection or agent.delegate
        |
inspect direct + delegated execution evidence
        |
finish turn
        |
begin turn: implement/verify
        |
edit -> command/process -> git.diff
        |
compare task acceptance criteria against objective evidence
```

The worktree and EngineeringSession remain the same across turns. A new model
conversation is not required for each iteration.

## Evidence authority rules

1. SceneWorks-captured file/Git/command/process/runtime observations are evidence.
2. Agent and model conclusions are inference.
3. Git remains authority for repository state; evidence references/hashes it.
4. Evidence is attributable to session, optional Task, turn and action.
5. Evidence collection must not silently broaden filesystem/process permission.
6. The ledger is not a repository mirror; source/diff duplication is avoided.
7. Evidence payloads are bounded to prevent uncontrolled database growth.

These rules are also recorded in repository `AGENTS.md` so later work packages
cannot quietly regress the authority model.

## Explicit non-goals for WP15

WP15 does **not** implement the PCS semantic runtime adapter. In particular it
does not yet add:

- PCS run profiles;
- `pcs.start/stop/restart/status`;
- PCS structured log parsing;
- crash/minidump capture;
- PCS health checks;
- external recording/asset roots;
- PCS runtime-state API projection;
- screenshot/GUI automation;
- background durable process/log capture independent of MCP observation.

Those features build on this evidence substrate rather than inventing a second
PCS-specific evidence system.

## Qualification requirements

Deterministic tests must verify at least:

- migration from WP14 schema to WP15 schema;
- task binding rejects cross-project tasks;
- only one ACTIVE turn exists per EngineeringSession;
- file reads/writes record hashes without persisting source content;
- command evidence records executable/args/exit/stdout/stderr/timestamps;
- process evidence records PID/lifecycle/output;
- Git diff returns changed-file hashes while persisted evidence omits full diff;
- `get_task` exposes linked EngineeringSession evidence summaries;
- delegated Execution events retain turn/action correlation;
- evidence/summary retrieval is bounded and paged.

Repository CI remains the merge gate: the non-live backend suite, deterministic
qualification suite and frontend production build must all pass.
