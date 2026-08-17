# WP0 — Baseline and contract audit

Audit performed 2026-08-17 against commit `8c673fe` (master).

This document is the **verified baseline** against which WP1–WP8 changes are
measured. Everything below was established from source, tests, Git history and
actual runtime probes — not from README claims, comments or previous reports.

Where a claim could be checked by running something, it was run. Commands and
their real output are quoted.

---

## 1. Canonical version

The version was contradictory across five locations. Resolved from Git history
rather than by unifying the numbers blindly.

| Location | Value found | Verdict |
| --- | --- | --- |
| `backend/pyproject.toml` | `3.0.0` | **canonical** |
| `backend/app/main.py` FastAPI `version=` | `2.5.2` | stale |
| `README.md` H1 title | `SceneWorks V2.5.2` | stale |
| `backend/app/domain/permissions.py` docstring | "as of V3.0" | consistent with 3.0.0 |
| `backend/app/execution/recovery.py` docstring | "Before V3.0 they…" | consistent with 3.0.0 |
| `web/package.json` | `1.0.0` | independent frontend version, undocumented |

**Evidence.** `git log -L '/^version/,+1:backend/pyproject.toml'` shows the bump
was deliberate:

```
4cc41ac fix: establish SceneWorks V3 baseline
-version = "2.5.1"
+version = "3.0.0"
```

Commit `4cc41ac` set pyproject to `3.0.0` and updated the `permissions.py` and
`recovery.py` docstrings to say V3.0, but did not update the README title or the
FastAPI `version=` string. Two later commits (`f3295a1`, `bf47a56`) continued V3
work (WP-WEB-1, WP-WEB-2) without revisiting either.

**Conclusion: `3.0.0` is the canonical version.** The README title and
`main.py` are stale and must be corrected to match, not the other way round.
`web/package.json` is a separate artifact version; if it stays independent that
needs documenting (WP3).

---

## 2. Test baseline

Measured, not claimed.

```
$ cd backend && python -m pytest -q
136 passed, 36 warnings in 431.56s (0:07:11)
```

| File | Tests | Area covered |
| --- | --- | --- |
| `tests/test_api.py` | 21 | REST surface, task/project CRUD, actions |
| `tests/test_domain.py` | 21 | task state machine, permissions (class-based) |
| `tests/test_memory.py` | 23 | memory CRUD, search, retrieval, provenance |
| `tests/test_gemini_acp.py` | 21 | ACP adapter against `mock_acp_server.py` |
| `tests/test_workflow_graph.py` | 14 | LangGraph topology, repair loop, idempotency |
| `tests/test_git_workspace.py` | 13 | worktree lifecycle, diff, commit capture |
| `tests/test_engine.py` | 10 | execution lifecycle, cancel, recovery |
| `tests/test_openhands.py` | 9 | OpenHands adapter (mocked SDK) |
| `tests/test_cors.py` | 4 | CORS headers |
| **total** | **136** | |

Frontend E2E (Playwright, not run in this audit — counted statically):
`full-workflow` 13, `reliability` 7, `responsiveness` 5, `visual-polish` 5,
`work-journeys` 5 = **35 E2E tests**.

**Baseline observation:** 431s for 136 tests is slow for a unit/integration
suite. The ACP and workflow-graph tests dominate. Worth measuring before CI is
added (WP3), because a 7-minute suite shapes what push-triggered CI can do.

**Coverage gaps identified:** no migration tests (nothing to migrate), no
qualification/evaluation tests, no policy tests, no Git-provenance query tests,
no test exercising triage routing with a real (scripted) triage decision — see
finding F3.

---

## 3. Actual workflow contract

Verified from `backend/app/workflows/manager.py` (1873 lines) and
`backend/app/domain/task_states.py`.

**Task states (12):** `NEW`, `ARCHITECTURE_ANALYSIS`,
`AWAITING_ARCHITECTURE_APPROVAL`, `READY_TO_IMPLEMENT`, `IMPLEMENTING`,
`TESTING`, `REVIEWING`, `CHANGES_REQUESTED`, `READY_FOR_HUMAN`, `ACCEPTED`,
`REJECTED`, `FAILED`, `CANCELLED`.

**LangGraph nodes (9):** `route_entry`, `triage`, `advisor_router`, `product`,
`cto`, `technical_expert`, `architect`, `architecture_approval`, `engineer`,
`reviewer`.

**Commit pinning:** confirmed working. `_run_triage` resolves the base commit at
the first node that touches the repository and writes it to `task.base_commit`;
`_prepare_and_start_architect`, `_prepare_and_start_engineer` and
`_prepare_and_start_reviewer` all reuse `task.base_commit` rather than
re-resolving. Architecture, implementation and review therefore describe the
same snapshot. **Invariant holds.**

**Worktree isolation:** confirmed. Every role runs in a worktree created by
`GitWorktreeService` — detached for read-only roles (`-triage`, `-{role}`,
`-review` suffixes), branch-based (`sw-task-{id}`) for the Engineer. No role
receives `project.repository_path` as its `cwd`. **Invariant holds.**

One documented exception: `PromptBuilder._read_project_context` falls back to
`project.repository_path` when `worktree_path` is None. That path is reachable
only for task-less conversational company asks, and it is a *read* of context
files, not an agent working directory. Noted for the WP4/security audit; it is
not a violation of "agents never operate in the human working tree" as long as
asks continue to pass a worktree, which `CompanyService` does.

**Human approval:** confirmed mandatory. `architecture_approval` uses LangGraph
`interrupt()`; the graph cannot proceed to `engineer` without a resume carrying
an explicit action. **Invariant holds.**

**Permission enforcement — actual behaviour.** Only two of the seven permission
flags gate anything at runtime:

- `REPOSITORY_WRITE` → `AgentPolicy.allow_write`; ACP proxy refuses `fs/write_text_file`.
- `SHELL_EXECUTE` → `AgentPolicy.allow_shell`; ACP proxy refuses `terminal/create`.

`REPOSITORY_READ`, `GIT_COMMIT`, `NETWORK_ACCESS`, `RESEARCH` and
`TASK_STATE_CHANGE` are declarative only. `permissions.py` already documents
this honestly, including that shell is the wide one and is not OS-sandboxed.
**No documentation defect here** — this is the rare case where the code comments
are more accurate than the README.

---

## 4. Findings matrix

| # | Claim | Current implementation | Evidence | Gap | Required action |
| --- | --- | --- | --- | --- | --- |
| **F1** | README: "Evaluation framework: repeatable scenario-based evaluation for bug fixes, features, refactors, architecture decisions, and more" | `backend/evaluation/__init__.py` is the only file. `run_scenario()` calls `build_context()`, sets `routing_correct = True` unconditionally, swaps in a fake backend, and sets `passed = len(errors) == 0`. **It never invokes a workflow.** | Ran it: 8/8 PASS in 5.1s, exit 0, while printing `implementation: INCORRECT` for all 8 scenarios. See §5. | The evaluation framework evaluates nothing. It passes iff `build_context` does not raise. It cannot fail for a wrong engineering outcome. | **WP1** — rebuild as a real outcome evaluation system. Highest priority. |
| **F2** | Docstring: "Usage: `uv run python -m evaluation.runner --scenario bug-fix --backend fake`" | No `runner.py` exists. `python -m evaluation.runner` → `No module named evaluation.runner`. `python -m evaluation` → `'evaluation' is a package and cannot be directly executed`. | Both commands run; both fail. | The documented CLI entry point does not exist. | **WP1** — provide a real CLI with documented exit codes. |
| **F3** | Triage classifies requests and selects participants | `_run_triage` short-circuits when `role_backend == "fake"`: it hard-codes `use_architect=True, requires_implementation=True` and skips the model call entirely (`manager.py:296`). | Source. `test_graph_v22_triage_defaults_fake_backend` asserts the bypass rather than triage behaviour. | **Triage routing is untestable deterministically.** No automated test exercises a real triage decision, so "routing correctness" and "correctly decide some requests need no implementation" are unverified. | **WP1** — make the fake backend able to script triage output so routing is evaluable without a live model. |
| **F4** | Project Memory: relevant accepted decisions are retrieved and injected | `get_relevant()` builds `f"%{query.strip()}%"` from the **entire task description** and uses it as one SQL `ILIKE` pattern (`memory.py:221`). `_inject_memory` passes `task.description or task.title`. | Runtime probe: a realistic task description returned **0** memories; the same store returned 1 for `"point cloud"` and 2 for `""`. See §6. | **Project Memory is dead on the real workflow path.** Any task description longer than a phrase retrieves nothing. Success criterion 3 is currently false. | **WP2** — deterministic token-based retrieval + regression tests using realistic descriptions. |
| **F5** | Memory retrieval is tested (23 tests) | Every retrieval test queries a single word: `"decided"`, `"API"`, `"Big"`, or omits the query. | `tests/test_memory.py:216-290`. | The test suite is shaped so F4 cannot surface. Tests validate the substring mechanism, not the retrieval contract. | **WP2** — add regression tests with multi-word task descriptions. |
| **F6** | Reviewer detects defects and requests changes | `parse_review_verdict()` returns `APPROVED` for **any non-empty text** lacking a verdict line (`services/workflow.py:126-134`). | Source. Empty output correctly → `CHANGES_REQUESTED`; truncated or verdict-less critical output → `APPROVED`. | **False-approval path.** A reviewer that says "this is broken" but omits the verdict line is recorded as approving. | **WP1** measure reviewer false approval; **WP4** make the verdict contract explicit. |
| **F7** | Schema is durable persistent state | `init_db()` calls `Base.metadata.create_all` only (`db/session.py:48`, comment: "V1 uses create_all; no Alembic yet"). No `alembic/`, no `migrations/`. | `ls backend/alembic* backend/migrations` → nothing. | Adding a column silently does nothing to an existing DB; the app then fails at query time. No upgrade path, no backup/restore docs. | **WP3** — versioned migrations. |
| **F8** | Backend and frontend validation run automatically | No `.github/` directory at all. | `ls .github/workflows/` → no such directory. | No CI. Every check is manual. | **WP3** — push-triggered CI on `master` (development happens directly on master). |
| **F9** | `model_profile` selects the model per role | `model_profile` is stored on `RoleDefinition` and copied to `Execution`, then passed to `AgentRequest`. `definitions.py:10-15` states plainly it is "Guidance-only metadata … NOT bound to backend model selection". | Source. Actual model comes from `SCENEWORKS_GEMINI_MODEL` / backend defaults. | Roles that need strongest reasoning get whatever the backend is configured with. Honest in code, but the capability does not exist. | **WP8** — real profile→backend/model mapping. Low priority; correctly deferred. |
| **F10** | `manager.py` is thin LangGraph orchestration | 1873 lines carrying graph construction, routing, 9 node bodies, execution preparation, task persistence, worktree lifecycle, result parsing, review/repair lifecycle, recovery, memory injection, and company-artifact storage. | Largest file in the backend by 2×. | Real responsibility sprawl, not merely size. But refactoring it is unsafe until a regression barrier exists. | **WP7** — after WP1 qualification coverage exists. |
| **F11** | Project policies / engineering contracts | Partially present but unstructured: `PromptBuilder._read_project_context` reads `AGENTS.md`, `ARCHITECTURE.md`, `CONTRIBUTING.md`, `ROADMAP.md` plus `project.architecture_context_paths`, as free text under "Project context files (authoritative reference)". `project.test_commands` / `build_commands` appear in the prompt for all roles. | `prompts.py:22`, `prompts.py:273-323`, `prompts.py:146-153`. | Context files are advisory prose. Nothing is structured, nothing is enforced, and the Reviewer gets no checkable criteria distinct from what the Engineer got. | **WP4** — structured policy available identically to all roles; Reviewer validates against it. |
| **F12** | Initiative/work-package hierarchy | `InitiativeState` is the LangGraph state TypedDict for a **single task**. There is no Initiative model, table, or API. Hierarchy is `Project → Task`. | `workflows/state.py`, `models.py` (7 tables: Project, Task, Execution, Event, Artifact, AppSetting, ProjectMemory). | The name "Initiative" is already taken by per-task graph state — a naming collision to resolve before WP5 adds a real Initiative. | **WP5** — note the collision. |
| **F13** | Git provenance is first-class | Good primitives exist: `diff`, `list_commits`, `head_commit`, `status`, `commit_all`, worktree lifecycle. Task stores `base_commit`, `result_commit`, `task_branch`. | `git/workspace.py`, `models.py:67-74`. | No queryable provenance *services*: cannot ask "which previous tasks touched these files", "which accepted decisions relate to this subsystem", "what changed between two baselines". Changed-file lists are not persisted. | **WP6** — deterministic provenance query services over Git as authoritative evidence. |
| **F14** | README: "The test suite uses an in-memory SQLite database" | `conftest.py` uses a file-based DB at `tmp_path / 'test.db'`. | `tests/conftest.py:26`. | Minor doc inaccuracy. | **WP3** docs pass. |
| **F15** | `recovery.py` documents recovery semantics | The module is a **docstring only** — 51 lines, zero code. | `execution/recovery.py`. | Not a defect: the actual recovery code lives in `engine.recover_interrupted()` and `manager.recover_workflows()`, and the docstring's claims match what those do (verified line by line). But documentation-as-module is easy to let drift, and nothing tests the claims. | **WP3** — add recovery tests so the doc is enforced rather than asserted. |

---

## 5. Evidence: the evaluation framework passes while failing

```
$ cd backend && PYTHONPATH=. python evaluation/__init__.py
Running scenario: bug-fix ...
  PASS (0.5s)
...
Passed: 8/8

  [PASS] bug-fix
    implementation: INCORRECT
  [PASS] small-feature
    implementation: INCORRECT
  [PASS] multi-file-feature
    implementation: INCORRECT
  [PASS] refactor
    implementation: INCORRECT
  [PASS] architecture-decision
    implementation: INCORRECT
  [PASS] investigation
    implementation: INCORRECT
  [PASS] technology-decision
    implementation: INCORRECT
  [PASS] review-repair
    implementation: INCORRECT

=== EXIT CODE: 0 ===
```

Eight scenarios "pass" in 5.1 seconds — less time than a single agent execution
takes — each one simultaneously reporting that its implementation is incorrect.
`ScenarioResult.passed` is computed as `len(self.errors) == 0`, and
`implementation_correct` is never assigned, so the report is internally
contradictory by construction.

`routing_correct = True` is a literal assignment (`__init__.py`, in
`run_scenario`). `requirement_quality` and `architecture_usefulness` are the
string `"not evaluated"`. `cost_estimate` is `"N/A (FakeBackend)"`.

This is the failure mode WP1 exists to remove: **the suite cannot fail for a
wrong engineering outcome, only for a Python exception.**

---

## 6. Evidence: Project Memory retrieves nothing for real task descriptions

Probe against a fresh DB with two accepted memories
("Point cloud IO goes through the api facade", "Public Python API is frozen"):

```
=== realistic multi-word task description ===
query = Add support for loading LAS files in the point cloud importer so tha...
get_relevant       -> 0 results  []
injection_context  -> 0 memories injected_ids=[]

=== single word (what the tests use) ===
get_relevant('point cloud') -> 1 results ['Point cloud IO goes through the api facade']

=== empty query (no filter at all) ===
get_relevant('') -> 2 results ['Public Python API is frozen', 'Point cloud IO goes through the api facade']
```

The store contains directly relevant accepted decisions. A realistic task
description retrieves **zero** of them, because the whole sentence becomes one
`ILIKE '%…%'` pattern that no stored content contains verbatim.

Both memory injection sites in `manager.py` (triage at `:309`, architect at
`:1050`) pass `task.description or task.title`. In production, with real task
descriptions, memory injection is a no-op — while emitting no signal that it
found nothing useful.

---

## 7. Invariants verified as holding at baseline

Checked against source, not assumed:

- SceneWorks is a standalone application; no SceneWorks code is written into managed repos.
- Agents never operate in the human working tree (every execution `cwd` is a worktree).
- Repository-grounded work is commit-pinned (`task.base_commit` resolved once, reused by all roles).
- Human approval is the final authority (`interrupt()` gate before `engineer`).
- No automatic merge into the user's branch (no merge/push code path exists).
- Agent providers sit behind `AgentBackend` (`fake`, `gemini_acp`, `openhands`).
- LangGraph is imported only by `app/workflows/*`. Verified: `execution/`, `git/`, `agents/`, `events/`, `domain/`, `roles/`, `services/` contain no LangGraph import.
- Roles are separate from provider/backend configuration (`RoleDefinition.backend` is config).
- Accepted decisions retain provenance (`source`, `source_task_id`, `source_execution_id`, `supersedes_id`).
- Speculative output does not become accepted truth automatically (`create()` defaults `status="proposed"`; `get_relevant` filters to `accepted`).

**One invariant is technically upheld but practically void:** accepted memories
are filtered correctly and never silently promoted — but they are also never
retrieved (F4), so the injection path is safe by virtue of being inert.

---

## 8. WP0 closure

A verified baseline now exists:

- canonical version resolved from history: **3.0.0**
- measured test baseline: **136 backend tests passing in 431s**, 35 E2E tests present
- actual workflow states, nodes, permission behaviour and commit-pinning documented above
- 15 findings recorded with evidence, each mapped to a work package
- 10 architectural invariants verified as holding

Two findings are severe enough to change the roadmap's emphasis:

- **F1/F2** — there is currently no qualification capability at all. The
  existing framework is worse than absent, because it reports PASS.
- **F4** — Project Memory, a shipped and documented feature, does not work on
  the path that matters.

**Proceeding to WP1** is correct and safe: it adds a measurement system without
modifying production behaviour, and it is the regression barrier every later
work package (especially WP7) depends on.
