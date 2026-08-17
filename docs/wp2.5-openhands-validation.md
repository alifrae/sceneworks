# WP2.5 — OpenHands activation and live validation

Performed 2026-08-17 against commit `dedea3c` (master), on **Windows 11 Pro
10.0.22631**, Python 3.12.10.

This is a validation record, not a design document. Every claim below is backed
by something that was run; where something could not be validated, it says so
rather than describing intent.

**Final status: EXPERIMENTAL.** Real OpenHands execution works and one mode is
live-validated, but a material capability gap remains on this platform.
Gemini ACP remains the default and the only backend able to run the Engineer here.

---

## 1. Environment detected

| Item | Finding |
| --- | --- |
| `openhands-sdk` installed at audit start | **No** — `ModuleNotFoundError: No module named 'openhands'` |
| `openhands-tools` installed at audit start | No |
| `openhands` executable | **Not on PATH** |
| Agent Server running | **No** — ports 3000, 8000, 8010, 8080, 3001, 8501 all closed |
| `SCENEWORKS_OPENHANDS_URL` | Not set |
| `SCENEWORKS_OPENHANDS_MODEL` | Not set |
| `backend/.env` | Does not exist |
| Docker | Installed but **daemon not running** (`npipe:////./pipe/dockerDesktopLinuxEngine` unreachable) |
| WSL2 | Available (Ubuntu, version 2) |
| LLM secrets configured | **No** provider credential present. Only `GOOGLE_CLOUD_PROJECT` and `POSTHOG_API_KEY` exist, neither of which is an LLM key. |
| Local LLM endpoint | **Yes** — LM Studio serving on `127.0.0.1:1234` |
| Local models | 4 present, all `not-loaded`; only `google/gemma-4-e2b` (VLM, 2B effective, `tool_use`) is agentic-capable |
| Gemini CLI (baseline backend) | Present, **0.55.1**, `health()` reports available |

Secrets are reported as present/absent only; no values were read or printed.

**Starting position: OpenHands was neither installed nor configured, and no
Agent Server existed.** Nothing about it had ever been executed.

### Installability

| Question | Answer |
| --- | --- |
| Is `openhands-sdk` on PyPI? | Yes — versions 1.0.0 … 1.42.1 |
| Does the latest install? | **No.** `ResolutionImpossible` |
| Which pair installs? | **`openhands-sdk==1.17.0` + `openhands-tools==1.17.0`** |

The blocker is upstream, not SceneWorks:

```text
lmnr 0.7.56 depends on opentelemetry-semantic-conventions==0.60b1
opentelemetry-instrumentation 0.65b0 depends on opentelemetry-semantic-conventions==0.65b0
opentelemetry-instrumentation 0.64b0 depends on opentelemetry-semantic-conventions==0.64b0
... (no release pins 0.60b1)
ERROR: ResolutionImpossible
```

`openhands-sdk` requires `lmnr`, which pins a semantic-conventions version no
`opentelemetry-instrumentation` release matches. pip therefore backtracks to
**1.17.0**, the newest SDK whose dependency set resolves.

**Installing the extra is not side-effect-free.** It adds 150 packages
(56 → 206) and changes 6 pre-existing pins:

| Package | Before | After |
| --- | --- | --- |
| pydantic | 2.13.4 | **2.12.5** (downgrade) |
| pydantic_core | 2.46.4 | 2.41.5 (downgrade) |
| anyio | 4.9.0 | 4.12.1 |
| click | 8.4.2 | 8.3.1 |
| requests | 2.33.1 | 2.33.0 |
| typing_extensions | 4.16.0 | 4.15.0 |

The full backend suite passes afterwards, so the downgrade is tolerable — but it
is why OpenHands stays an **optional extra**, never a default dependency.

---

## 2. Active integration mode

`local` — openhands-sdk in-process against a `LocalWorkspace`, **no Agent Server
required**.

This mode did not exist before WP2.5. The old adapter entered SDK mode only when
`SCENEWORKS_OPENHANDS_URL` was set, so the one path that works on this machine
was unreachable. `openhands.sdk.Workspace` is a factory:
`Workspace(working_dir=…)` returns a `LocalWorkspace`, and
`Workspace(host=…, working_dir=…)` a `RemoteWorkspace` — verified directly.

| Mode | Requires | Validated |
| --- | --- | --- |
| `local` | sdk + tools + a model | **yes** (read-only roles) |
| `remote` | the above + Agent Server URL | no — none available |
| `http` | Agent Server URL, no SDK | no |
| `cli` | `openhands` executable | no — not installed |

---

## 3. Defects found

Each was verified against the real installed SDK, not inferred.

### D1 — `openhands.tools` was never declared as a dependency

**Symptom.** `from openhands.tools.preset.default import get_default_agent`
→ `ModuleNotFoundError: No module named 'openhands.tools'`, even with
`openhands-sdk` installed.
**Root cause.** `openhands-tools` is a separate distribution; `pyproject.toml`
declared only `openhands = ["openhands-sdk"]`.
**Fix.** The extra now installs both, pinned to the same version.
**Regression test.** `test_mismatched_tools_package_does_not_resolve_to_local`.

### D2 — a mismatched tools/sdk pair looked usable

**Symptom.** `openhands-tools 1.42.1` installs cleanly against
`openhands-sdk 1.17.0`, then raises
`ModuleNotFoundError: No module named 'openhands.sdk.utils.path'` at import.
**Root cause.** The adapter probed with `importlib.util.find_spec`, which reports
a module present without importing it.
**Fix.** `_module_available()` performs a real import; only a successful import
counts. Version equality is documented and pinned.
**Regression test.** `test_mismatched_tools_package_does_not_resolve_to_local`.

### D3 — an SDK import failure silently became HTTP mode

**Symptom.** `except ImportError: return await self._run_http(...)` — any SDK
import problem redirected execution to REST polling against a server that may not
exist, and told nobody.
**Root cause.** Fallback used as error handling.
**Fix.** Modes are resolved explicitly by `resolve_mode()` and emitted as a
`backend.mode` event on every run. An SDK import failure now fails with the
reason.
**Regression tests.** `test_sdk_import_failure_does_not_silently_become_http`,
`test_run_always_reports_the_mode_it_chose`.

### D4 — `LLM()` with no model raised a raw pydantic error

**Symptom.** `LLM()` → `ValidationError: model must be specified in LLM`,
surfaced to the operator as `"OpenHands SDK error: ..."`.
**Root cause.** `llm = LLM(**kwargs) if kwargs else LLM()` treated the model as
optional; the SDK requires it.
**Fix.** A model is mandatory. `health()` reports unavailable without one and
`run()` fails with the variable name to set.
**Regression tests.** `test_health_is_unavailable_without_a_model`,
`test_run_without_a_model_fails_with_a_clear_message`.

### D5 — no way to point OpenHands at an OpenAI-compatible endpoint

**Symptom.** The adapter never passed `base_url`, and no setting existed for it.
**Root cause.** `openhands_url` (Agent Server) was conflated with the LLM
endpoint. Without `base_url`, only hosted providers were reachable — and no
provider credential exists here, so **no live validation was possible at all**.
**Fix.** `openhands_base_url` / `SCENEWORKS_OPENHANDS_BASE_URL` added. This is
what made the entire live validation possible.

### D6 — SDK mode could never emit events or a summary

**Symptom.** `for event in getattr(conversation, "events", [])` — the loop body
never executed.
**Root cause.** `LocalConversation` has **no `events` attribute** (verified: its
public API is `ask_agent, close, condense, … pause, run, send_message, state, …`).
The `getattr` default silently yielded `[]`, so SDK mode produced zero events and
always returned the placeholder `"OpenHands SDK completed."`.
**Fix.** Events are captured through the SDK's `callbacks=` parameter into a
thread-safe queue and mapped as they arrive. The summary is built from the
agent's own `MessageEvent`s.
**Regression tests.** `test_summary_comes_from_agent_messages`, plus the seven
event-mapping tests.

### D7 — the synchronous SDK ran on the event loop

**Symptom.** `conversation.send_message(...)` and `conversation.run()` were
called directly inside an `async def`, blocking the API event loop for the entire
execution — freezing SSE and every HTTP request.
**Root cause.** A synchronous library called from async code.
**Fix.** The conversation runs in `asyncio.to_thread`, with a concurrent drainer
translating queued events.

### D8 — cancellation was decorative

**Symptom.** `cancel()` set an asyncio Event and called `close()`. Because
`run()` was blocking the loop, neither could be observed until the run had
already finished; and the cancellation checks sat *after* `run()` returned.
**Root cause.** D7, plus using `close()` where the SDK's cooperative stop is
`pause()`.
**Fix.** `cancel()` calls `pause()` then `close()` off-loop.
**Regression tests.** `test_cancel_calls_pause_then_close_on_the_conversation`,
`test_cancel_survives_a_raising_conversation`. **Live evidence:** the log line
`Agent execution pause requested` from a real timed-out run.

### D9 — health reported available on configuration alone

**Symptom.** `_health_server` returned `available=True` for any HTTP 200 from
`/health`.
**Root cause.** Reachability treated as capability.
**Fix.** `health()` now verifies imports, requires a model, probes the LLM
endpoint when configured, reports the resolved mode, whether that mode is
validated, and whether shell is available.
**Regression tests.** `test_health_is_unavailable_without_a_model`,
`test_health_fails_when_the_llm_endpoint_is_unreachable`,
`test_health_reports_missing_shell_support_on_this_platform`,
`test_remote_health_marks_the_mode_unvalidated`.

### D10 — the docstring claimed a safety check that did not exist

**Symptom.** *"If OpenHands cannot guarantee the required workspace boundary for
a given configuration, this adapter rejects the configuration rather than
weakening SceneWorks safety."* No such rejection existed anywhere in the file.
**Fix.** Claim removed. The real boundary is documented in §6 and in
`limitations.md`.

### D11 — "WebSocket streaming" was never implemented

**Symptom.** README and docstring advertised SDK/WebSocket streaming. The code
ran to completion and then read events, so nothing streamed.
**Fix.** Streaming now genuinely happens via SDK callbacks; the docs describe the
actual mechanism.

### D12 — the agent's turn budget was unbounded

**Symptom.** A live read-only task ran **3h52m** without concluding
(`max_iterations: 500`, the SDK default), consuming its whole execution budget.
**Root cause.** SceneWorks never set `max_iteration_per_run`.
**Fix.** `openhands_max_iterations` (default 40) is passed to the Conversation.
**Regression tests.** `test_agent_iterations_are_bounded`,
`test_iteration_bound_is_configurable_and_never_zero`.

### D13 — the SDK's console visualizer crashed the run on Windows

**Symptom.** `UnicodeEncodeError: 'charmap' codec can't encode character
'\U0001f510'` before any work happened.
**Root cause.** The default visualizer prints emoji to a cp1252 console.
**Fix.** `visualizer=None` — SceneWorks renders events itself.

### D14 (introduced during WP2.5, then fixed) — blocking import probe

**Symptom.** After adding the import probe, `test_end_to_end_workflow` failed
with the task stuck in `IMPLEMENTING` and `workflow.failed reason: cancelled`.
**Root cause.** `_module_available()` imports `openhands.sdk`, which pulls
litellm and opentelemetry and takes seconds. Called inline from `health()` during
the startup warm-up, it blocked the event loop long enough to stall an in-flight
workflow.
**Fix.** The probe is cached process-wide and warmed via `asyncio.to_thread`.
**Regression test.** `test_module_probe_is_cached`.

---

## 4. Upstream blocker: no shell on Windows

```text
openhands/tools/terminal/terminal/factory.py:108
    raise NotImplementedError("Windows is not supported yet for OpenHands V1.")
```

The default agent has exactly three tools — `terminal`, `file_editor`,
`task_tracker` — and constructing the terminal tool raises on Windows. Everything
else works: `LocalWorkspace` is created, `FileEditor` initialises with the correct
worktree cwd, `LocalConversation` is constructed.

**Consequence.** In `local` mode on Windows, OpenHands can read and edit files but
cannot run commands. The Engineer role requires shell (to run tests and commit),
so **the Engineer cannot run on OpenHands on this platform**. SceneWorks now
refuses such a role up front with the reason and a pointer to Gemini ACP, instead
of failing deep inside the agent after the model has already been paid for.

Read-only roles remain fully usable: the adapter drops the terminal tool and emits
a `backend.tools_restricted` event so the restriction is visible.

---

## 5. Live validations

Every run below used the **real** OpenHands backend. No FakeAgentBackend result
appears anywhere in this section.

### 5.1 Health against the real installation

`test_live_health_reports_available` — **PASSED**.
Reported `available=True`, version `1.17.0`, `mode=local`, the configured model,
and the shell restriction.

### 5.2 Read-only execution (bare SDK probe)

| Field | Value |
| --- | --- |
| backend / mode | openhands-sdk 1.17.0, `local` |
| model | `lm_studio/google/gemma-4-e2b` @ `127.0.0.1:1234/v1` |
| scenario | read `calc/core.py`, report what `add()` returns, modify nothing |
| duration | **431 s** |
| events | 5 — `SystemPromptEvent` 1, `MessageEvent` 2, `ActionEvent` 1, `ObservationEvent` 1 |
| tools | `file_editor`, `task_tracker` (terminal removed) |
| result | run completed |
| changed files | none |
| git status | clean |
| HEAD unchanged | **yes** |

This is the run that proved a live path exists at all: real tool use
(`ActionEvent` → `ObservationEvent`), real output, nothing modified.

### 5.3 Read-only execution through the SceneWorks adapter

| Field | Value |
| --- | --- |
| backend / mode | openhands 1.17.0, `local` |
| scenario | read `app.py`, state what `main()` returns, modify nothing |
| duration | **13 940 s (3h52m)** |
| terminal status | **did not complete within budget** |
| real LLM traffic | yes — `POST http://127.0.0.1:1234/v1/chat/completions "HTTP/1.1 200 OK"` |
| tools loaded | `['file_editor', 'task_tracker']` — terminal correctly removed |
| worktree cwd | `…/test_live_read_only_execution_0/repo` — the SceneWorks worktree |
| cancellation | `Agent execution pause requested` — the timeout path invoked `pause()` |
| human checkout unchanged | **yes** |

**Assessment.** The adapter behaved correctly throughout — correct working
directory, correct tool restriction, real LLM calls, working cancellation. It did
not finish because `max_iterations` was still the SDK default of 500 (D12, fixed
after this run) and because a 2B reasoning model with `reasoning_effort: high` is
far too slow for the task. **This is an environment/model limitation, not an
adapter defect** — and it is why the classification is EXPERIMENTAL rather than
SUPPORTED.

### 5.4 Coding execution

**Not possible on this platform.** The Engineer requires shell; §4 applies. The
adapter refuses the role with the reason rather than attempting it. This is the
single largest gap and the decisive reason OpenHands is not SUPPORTED.

### 5.5 Live cancellation through qualification

| Field | OpenHands | Gemini ACP |
| --- | --- | --- |
| scenario | `cancellation` | `cancellation` |
| verdict | **PASS** | **PASS** |
| checks | 4/4 | 4/4 |
| duration | 34.0 s | 12.7 s |
| final status | `CANCELLED` | `CANCELLED` |
| cancellation honoured | true | true |
| base commit | `635a232a` | `ea140d62` |
| result commit | none | none |
| changed files | none | none |
| executions | 1 | 1 |
| backend failures | 0 | 0 |
| human interventions | 1 | 1 |
| version | 1.17.0 | 0.55.1 |

Cancellation was proven by observing the live execution stop and the task reach
`CANCELLED` with no commit — not by `cancel()` failing to raise.

---

## 6. Isolation findings

State plainly what enforces what. **This is not a sandbox.**

| Layer | What it actually enforces |
| --- | --- |
| **SceneWorks** | Creates the commit-pinned worktree and passes it as the agent's working directory. Chooses which tools the role may have (drops `terminal` where shell is unavailable). Never passes `project.repository_path` as an agent cwd. |
| **OpenHands (`local`)** | `FileEditor` is constructed with `cwd` set to the worktree and resolves paths against it. This is **library-level path handling, not containment**: the agent runs in the SceneWorks process with that process's full filesystem access. A tool that accepted an absolute path outside the worktree would not be stopped by SceneWorks. |
| **OS / container** | **Nothing.** No container, no chroot, no user separation, no seccomp. |
| **Trust assumption** | The OpenHands runtime and the model are trusted not to write outside the working directory. Verified empirically for the read-only runs above (worktree clean, HEAD unchanged), **not** enforced structurally. |

Compared with Gemini ACP, which mediates **every** file and shell request through
the ACP proxy and can refuse individual operations, OpenHands `local` mode offers
strictly weaker enforcement: directory scoping by convention versus per-request
approval.

**Remote mode has a distinct and more serious problem.** `working_dir` is a path
in the *Agent Server's* filesystem. A server in Docker or WSL does not see the
Windows SceneWorks worktree, so passing a local path either fails or silently
points the agent at an unrelated directory. Commit-pinned isolation cannot be
established that way. This is why remote mode is classified unvalidated rather
than merely untested, and why validating it would require design work (shipping
the worktree to the server, or mounting it) rather than configuration.

---

## 7. Event contract

Verified against live events. OpenHands payloads never leave the adapter; a test
asserts every emitted type is in `app.events.types.EVENT_LABELS`.

| SceneWorks event | Produced from | Observed live |
| --- | --- | --- |
| `agent.message` | `MessageEvent` | yes |
| `agent.thought_summary` | `ActionEvent.thought` | yes |
| `tool.started` | `ActionEvent` | yes |
| `tool.completed` | `ObservationEvent` | yes |
| `file.changed` | `ActionEvent` (editor tool) | yes |
| `command.started` / `command.output` | `ActionEvent` / `ObservationEvent` (terminal tool) | **no — no shell on Windows** |
| `agent.event` (severity error) | `AgentErrorEvent` | not triggered |
| `agent.event` (diagnostics) | everything else, by class name | yes |

**Deliberately not produced**, rather than fabricated:

- `test.result` — OpenHands reports no structured test outcome.
- `git.commit` — the agent would commit through the shell; SceneWorks captures
  the commit itself in `_finish_engineer`.

An unrecognised SDK event becomes a diagnostic `agent.event` carrying its class
name, so a new event type is visible instead of silently dropped.

---

## 8. Qualification integration

```bash
python -m evaluation --backend openhands --scenario bug-fix
python -m evaluation --backend openhands --live-subset
```

Rules that keep a live provider run honest:

- **A provider that is not usable is BLOCKED, never PASS.** The harness calls
  `health()` first and blocks the scenario with the health detail if unavailable.
- **Scripted-only scenarios are BLOCKED against a real backend.** Negative
  controls work by scripting a specific wrong behaviour, which a real model cannot
  be made to reproduce on cue; running them live would measure the model's mood.
  Four scenarios are marked `live_capable`: `architecture-investigation`,
  `no-implementation-needed`, `bug-fix`, `cancellation`.
- **A live run declares no required scenarios**, so a live PASS can never be
  mistaken for a release gate. Only the deterministic suite gates a release.
- **Metrics that stop being measurable live are declared unsupported**
  (`reviewer_false_approval`, `repair_iterations`) rather than asserted.
- The backend version and health detail are recorded in the report, so a result is
  attributable to a concrete version and mode.

Live tests in pytest are marked `live` and `openhands` and skip cleanly when the
provider is not configured. They never fall back to FakeAgentBackend.

---

## 9. Final status: EXPERIMENTAL

| Closure criterion | Evidence |
| --- | --- |
| 1. Installation/configuration status known | §1 — nothing installed or configured at start |
| 2. Active mode identified | §2 — `local` |
| 3. Health tested against a real installation | §5.1 — passed, version 1.17.0 |
| 4. Real read-only execution ran | §5.2, §5.3 — real LLM traffic, real tool use |
| 5. Real coding execution ran | **No** — impossible on Windows (§4) |
| 6. Expected result produced | Partially — §5.2 yes; §5.3 exceeded budget |
| 7. Human working tree unchanged | §5.2, §5.3, §5.5 — yes in every run |
| 8. Confinement/trust boundary tested and documented | §6 |
| 9. Events observed through SceneWorks | §7 |
| 10. Live cancellation tested | §5.3, §5.5 — `pause()` observed; task reached CANCELLED |
| 11. A qualification scenario ran on the real backend | §5.5 — `cancellation`, 4/4 PASS |
| 12. Identical scenario compared with Gemini ACP | §5.5 — identical outcomes |
| 13. Explicit classification | **EXPERIMENTAL** |
| 14. Documentation matches evidence | this file, `backends.md`, `limitations.md`, `.env.example` |
| 15. No fake result presented as validation | §5 uses the real backend throughout |

**Why not SUPPORTED.** Criterion 5 fails outright: no coding execution is
possible on Windows, so the Engineer — the role that produces the Git changes
SceneWorks exists to manage — cannot use this backend here. Criterion 6 is only
partial.

**Why not UNAVAILABLE.** OpenHands *is* installable and *did* execute real work:
real LLM calls, real tool use, real events, working cancellation, and a
qualification scenario passing against the real backend.

**Why not BROKEN.** The adapter is now compatible with the real SDK API. The 14
defects found were localized to `openhands.py` and its settings, and are fixed.

**Validated configuration, stated exactly:**

> openhands-sdk 1.17.0 + openhands-tools 1.17.0, `local` mode, Windows 11,
> LM Studio (`google/gemma-4-e2b`) as the LLM endpoint, read-only roles only.

No other mode, platform, or model is claimed as validated.

### Remaining gaps

**Accepted:**
- No OS-level sandbox in `local` mode (§6). Container isolation remains future
  work.
- `local` mode weaker than Gemini ACP on permission enforcement (§6).

**Deferred:**
- `remote` mode needs design work for the path-domain problem (§6), not just
  configuration.
- `http` and `cli` modes remain implemented-but-unvalidated.
- Newer openhands-sdk releases are blocked upstream (§1); revisit when `lmnr`
  loosens its opentelemetry pin.

**Environment, not defects:**
- The only local model is far too slow for real SceneWorks tasks (431 s for one
  file inspection). A useful OpenHands deployment here needs either a faster local
  model or a hosted provider credential.

---

## 10. Default backend policy

**Unchanged: Gemini ACP remains the default.** WP2.5 does not alter it, per its
own instruction, and the evidence independently supports keeping it — Gemini ACP
is the only backend that can run the Engineer on this platform.
`test_openhands_is_not_default_backend` asserts every default role stays on
`gemini_acp`.

OpenHands is a validated **opt-in** backend for read-only roles.
