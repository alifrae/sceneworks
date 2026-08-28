# WP17 — Managed PCS GUI Evidence

## Goal

WP17 adds objective visual evidence to the existing Advanced-control stack without
turning SceneWorks into a generic desktop-automation server.

The intended loop is:

```text
ChatGPT / supervisor
  -> EngineeringSession + EngineeringTurn
  -> SceneWorks semantic PCS control
  -> managed PCS process
  -> window/dialog observation + screenshot evidence
  -> code change / restart / deterministic verification
  -> before/after visual comparison
  -> acceptance decision from evidence
```

Gemini/OpenCode/OpenHands remain optional delegated workers. They are not required
to discover PCS windows, capture screenshots or compare visual state.

## Scope

WP17 provides observation only:

- enumerate top-level windows owned by the live SceneWorks-managed PCS PID;
- enumerate dialogs/owned windows for that same PID;
- capture one visible PCS window as PNG evidence;
- persist screenshot bytes outside the Git worktree;
- attach SHA-256, dimensions, capture method, run/session/task/turn/action
  correlation to the WP15 evidence ledger;
- retrieve persisted screenshots by evidence artifact id;
- compare two SceneWorks-generated screenshots deterministically;
- persist a pixel-difference image when screenshots differ.

WP17 does **not** provide:

- arbitrary desktop capture;
- discovery of unrelated applications/windows;
- focus or foreground-window control;
- mouse movement/clicks;
- keyboard/text injection;
- coordinate automation;
- accessibility/UIA invocation;
- OCR;
- semantic interpretation of screenshot contents.

Controlled GUI automation is a separate future boundary and must not be smuggled
into this observation package.

## MCP tools

Advanced mode adds:

```text
sceneworks.pcs.windows
sceneworks.pcs.dialogs
sceneworks.pcs.screenshot
sceneworks.pcs.gui_artifacts
sceneworks.pcs.gui_artifact
sceneworks.pcs.visual_compare
```

Fresh observation (`windows`, `dialogs`, `screenshot`) requires a live PCS run
owned by the same EngineeringSession. Persisted screenshots/diffs remain
retrievable and comparable after PCS stops or the EngineeringSession closes.

Screenshot and visual-diff retrieval use MCP image content (`image/png`) rather
than embedding large base64 blobs inside `structuredContent`.

## Permission model

WP17 adds one independent EngineeringSession permission:

```text
gui_observe
```

It grants only the observation capabilities listed above. It does not imply:

```text
process_control
shell_execute
repository_write
external_asset_read
future gui_automate authority
```

A normal visual-verification session that starts PCS itself therefore commonly
requests both:

```text
process_control
gui_observe
```

The project-level Advanced ceiling must also allow `gui_observe`.

## Managed-process confinement

SceneWorks does not accept an arbitrary PID from MCP.

For every fresh GUI observation it resolves the current `PcsRun` belonging to the
EngineeringSession and uses that managed run's PID internally:

```text
MCP session_id
  -> EngineeringSession
  -> current SceneWorks PcsRun
  -> managed OS PID
  -> windows belonging to that PID only
```

Opaque `window_id` values returned by `pcs.windows` are usable only while they
continue to identify a visible window returned for that managed PID. A caller
cannot supply a desktop-wide HWND/PID pair to escape this relationship.

The OS PID itself is not added to each public window record because the caller
already has the correlated managed-run identity from `pcs.status`.

## Capture semantics

The initial system provider supports a Windows SceneWorks host.

It uses Win32 window enumeration and captures the visible screen rectangle
occupied by the selected PCS window. The evidence explicitly records:

```text
capture_method = visible_screen_region_gdi
occlusion_safe = false
```

This matters. WP17 does not claim to reconstruct an obscured/minimized window.
If another window covers PCS, that can affect the screenshot. Visual acceptance
procedures should keep the target PCS window visible and unobstructed.

On a non-Windows host, the system provider returns an explicit unsupported error.
The service/provider boundary is platform-neutral so another deterministic
provider can be added later without changing MCP semantics.

## Artifact and evidence model

Screenshot bytes are **not** stored in Git and are **not** duplicated inside the
EngineeringEvidence JSON payload.

They are stored below SceneWorks' existing project-owned storage root:

```text
attachment_root/
  <project-id>/
    _gui/
      <engineering-session-id>/
        <generated-id>.png
```

The storage key is internal and is stripped from MCP artifact metadata.

The WP15 ledger stores, at minimum:

```text
EngineeringSession
Task (when bound)
EngineeringTurn (when supplied)
action_id
operation
run_id
SHA-256
size
MIME type
width / height
capture method
occlusion_safe
window metadata
label
timestamp
```

Project history purge already removes the project-owned storage tree, so GUI
artifacts are deleted together with their evidence records. External PCS
recordings remain unaffected.

## Deterministic visual comparison

`pcs.visual_compare` operates on two persisted SceneWorks-generated PNG
artifacts. It does not ask an LLM whether images "look the same".

When dimensions match, SceneWorks computes:

```text
changed_pixel_ratio
changed_bbox
identical
```

A pixel counts as changed when any RGB channel differs. If there is at least one
changed pixel, SceneWorks also stores an absolute per-channel difference PNG and
returns its artifact metadata/image.

If dimensions differ, the result records both dimensions and leaves pixel-ratio
comparison undefined instead of rescaling or guessing correspondence.

The numeric/hash result is objective evidence. Statements such as "the renderer
is fixed" or "this dialog is wrong" remain interpretation and must be evaluated
against acceptance criteria and other PCS evidence.

## API-first rule

WP17 does not weaken the WP16 rule:

> Prefer deterministic PCS APIs over GUI observation whenever PCS can expose the
> same state semantically.

Examples:

- playback state -> PCS runtime-state API, not screenshot inference;
- loaded recording -> PCS runtime-state API, not title-bar parsing;
- process health -> `pcs.health`, not "window exists";
- error details -> structured PCS logs/errors before screenshot interpretation.

Screenshots are especially useful when visual layout/rendering/dialog state is
itself part of the acceptance criteria or when a hardened PCS API cannot expose
the failure.

## Example verification flow

```text
engineering_session.begin_turn(intent="reproduce visual regression")
pcs.start(profile="debug")
pcs.health
pcs.windows
pcs.screenshot(label="before")

# inspect objective logs/runtime state and modify/delegate code as appropriate

pcs.restart
pcs.health
pcs.screenshot(label="after")
pcs.visual_compare(before_artifact_id=..., after_artifact_id=...)
engineering_session.evidence(category="gui")
engineering_session.finish_turn
```

The screenshots can then be visually inspected alongside the exact pixel metrics,
Git diff, tests, logs and runtime state.

## Security and privacy

Screenshots can contain sensitive engineering information visible inside PCS.
`gui_observe` must therefore be granted deliberately.

The confinement properties are:

- the MCP caller cannot choose an arbitrary process PID;
- only windows belonging to the current managed PCS PID are enumerated;
- screenshot storage remains SceneWorks-owned and project-scoped;
- storage paths are not exposed through MCP;
- artifact bytes are SHA-256 verified when retrieved;
- no input/automation capabilities are present in WP17.

This is still not an OS security sandbox. A separately granted shell/process
capability retains the OS-authority caveats documented by WP14/WP16.

## Acceptance criteria

WP17 is complete when:

1. `gui_observe` is an independent Advanced permission.
2. Fresh window/dialog/screenshot observation is restricted to the managed PCS
   PID.
3. Screenshots are persistent SceneWorks evidence with hashes and causal
   correlation and no host/storage-path leakage.
4. Stored GUI artifacts remain inspectable after the PCS run ends.
5. Before/after comparison produces deterministic pixel metrics and a persisted
   diff artifact when changed.
6. MCP returns image content without putting raw image bytes in
   `structuredContent`.
7. No generic desktop or GUI-control tools exist.
8. Linux/non-Windows deterministic tests use a fake provider; no live PCS GUI or
   paid model is required for CI.
9. Existing WP14-WP16 behavior and full qualification remain green.
