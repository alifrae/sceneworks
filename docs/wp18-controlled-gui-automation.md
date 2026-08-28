# WP18 — Controlled Managed-PCS GUI Automation

## Goal

WP18 adds a narrowly governed GUI-control fallback for PCS Advanced control. It
builds on WP17 visual evidence without turning SceneWorks into a generic desktop
automation server.

The intended authority order remains:

```text
PCS semantic/API control
  > deterministic SceneWorks runtime/process/log state
  > accessibility/UI Automation fallback
  > screenshot interpretation
  > agent/model conclusions
```

If PCS exposes a deterministic API for an operation, use that API. WP18 exists
for UI-only behavior that PCS cannot yet expose semantically.

## Scope

Advanced mode adds:

```text
sceneworks.pcs.controls
sceneworks.pcs.gui.invoke
sceneworks.pcs.gui.set_value
sceneworks.pcs.gui.select
sceneworks.pcs.gui.toggle
```

These operations target controls discovered under a **visible window belonging
to the live SceneWorks-managed PCS process**.

WP18 does not expose:

- arbitrary process IDs;
- arbitrary HWNDs;
- desktop-wide control discovery;
- coordinate clicking;
- pointer movement;
- keyboard injection;
- caller-provided PowerShell/script execution;
- OCR as an action target;
- generic automation of unrelated applications.

## Permission model

WP18 adds:

```text
gui_automate
```

Mutation requires both:

```text
gui_observe
gui_automate
```

This is intentional. `gui_observe` remains an independent read-only capability;
it never silently implies mutation. `gui_automate` cannot be used without the
observation capability because every mutation must produce objective before and
after evidence.

A session that launches PCS and automates it therefore commonly requests:

```text
process_control
gui_observe
gui_automate
```

The configured Advanced capability ceiling must allow the requested permissions.

## Control discovery and identity

`pcs.controls` uses the current managed PCS run to derive the OS PID internally,
selects one current visible PCS window, and enumerates accessibility controls
under that window only.

Each returned control includes:

```text
control_id
automation_id
name
control_type
enabled
offscreen
bounds
supported patterns
```

`control_id` is an opaque SceneWorks token containing the UI Automation runtime
identity plus the managed `window_id`. It is intentionally ephemeral. When the
UI rebuilds, the id may become stale.

Every mutation decodes the id, verifies that its window is still a current
visible window owned by the managed PCS PID, then re-resolves the UIA runtime id
**inside that window**. A stale/forged id fails instead of falling through to
another application.

## Windows UI Automation provider

The initial system provider targets a Windows SceneWorks host and uses Microsoft's
built-in .NET `UIAutomationClient` APIs.

SceneWorks invokes a **fixed internal PowerShell program** with:

```text
-NoProfile
-NonInteractive
-EncodedCommand <fixed SceneWorks script>
```

Dynamic data is JSON transported through a dedicated environment variable. MCP
callers never provide PowerShell source, expressions, PIDs or HWNDs.

The provider currently supports these UIA patterns:

| SceneWorks action | Windows UIA pattern |
| --- | --- |
| `pcs.gui.invoke` | `InvokePattern` |
| `pcs.gui.set_value` | `ValuePattern` |
| `pcs.gui.select` | `SelectionItemPattern` |
| `pcs.gui.toggle` | `TogglePattern` |

This avoids coordinate-based clicking and simulated keyboard entry.

The provider boundary remains platform-neutral. CI uses a deterministic fake
provider; another native provider can be added later without changing MCP
semantics.

## Evidence-first mutation contract

A GUI action is not a blind RPC.

SceneWorks performs:

```text
1. validate EngineeringSession + permissions
2. validate optional active EngineeringTurn
3. resolve live managed PCS run/window
4. capture durable pre-action screenshot
5. execute one accessibility action
6. allow bounded UI settle time
7. capture durable post-action screenshot
8. run WP17 deterministic visual comparison
9. persist final action evidence
10. return the resulting screenshot as MCP image content
```

The pre-action screenshot is mandatory. If it cannot be captured, SceneWorks does
**not** execute the mutation.

If the action executes but the post-action screenshot cannot be captured,
SceneWorks records:

```text
status = PARTIAL
action_execution_state = executed_but_unverified
```

and returns an error instead of claiming success.

If both screenshots exist but deterministic comparison fails, the action is also
recorded as partial/unverified.

This matters because GUI actions are not automatically reversible. Evidence must
make uncertain post-action state explicit.

## Visual evidence

Before and after images use the existing WP17 persistent artifact model:

```text
attachment_root/<project-id>/_gui/<engineering-session-id>/<id>.png
```

The storage path remains internal. MCP receives artifact metadata and the final
image through normal MCP image content.

`pcs.visual_compare` produces objective:

```text
changed_pixel_ratio
changed_bbox
identical
optional persisted pixel-difference image
```

These metrics prove what pixels changed, not whether the engineering intent was
satisfied. Acceptance still combines GUI evidence with task criteria, PCS runtime
state, logs, tests and Git truth.

## Text/value privacy

`pcs.gui.set_value` may be used for paths, configuration text or other sensitive
input. SceneWorks therefore does **not** persist the supplied value in the WP15
evidence payload.

It records only:

```text
value_chars
value_sha256
```

This preserves causal correlation without turning the evidence ledger into a
secret/value echo channel.

## Recommended usage

Use semantic PCS tools first:

```text
pcs.runtime_state
pcs.health
pcs.run_verification
```

Use WP18 only when the operation has no hardened PCS API, for example a UI-only
workflow or dialog that itself must be verified.

Example:

```text
engineering_session.begin_turn(intent="verify UI-only playback control")
pcs.start(profile="debug")
pcs.health
pcs.windows
pcs.controls(window_id=...)
pcs.gui.invoke(control_id=<Play accessibility control>)
engineering_session.evidence(category="gui")
engineering_session.finish_turn
```

The action result already includes before/after artifact metadata and deterministic
visual comparison. Additional `pcs.gui_artifact` calls can retrieve the preserved
before image or pixel-difference artifact.

## Security boundary

WP18 is deliberately narrower than general GUI automation:

- target PID is derived from SceneWorks' managed PCS run, not supplied by MCP;
- target window must currently belong to that PID;
- target control must be resolved as a descendant accessibility element of that
  window;
- no coordinate or keyboard primitive exists in MCP;
- `gui_automate` is independently permissioned;
- observation evidence is mandatory around mutation;
- provider script text is fixed in SceneWorks source;
- caller-provided GUI values are not persisted verbatim.

This is an application-governance boundary, not an OS sandbox. A separately
granted `shell_execute` capability still carries the WP14 OS-authority caveats.

## Live Windows qualification

Linux CI deterministically validates policy, evidence correlation, MCP contracts,
permission enforcement, before/after behavior and partial-action semantics using
provider fakes.

That does **not** prove that every PCS/Qt widget exposes a useful UI Automation
pattern on a real Windows desktop. Windows host validation should verify the real
PCS accessibility tree. Controls that do not expose UIA patterns remain
unautomatable through WP18 until PCS accessibility or a semantic PCS API is
improved; SceneWorks must not silently fall back to coordinate clicking.

## Acceptance criteria

WP18 is complete when:

1. `gui_automate` is an independent Advanced permission and mutation requires
   both `gui_observe` and `gui_automate`.
2. UI controls are discovered only beneath a current visible managed PCS window.
3. No arbitrary PID/HWND/coordinate/pointer/keyboard mutation primitive is
   exposed.
4. Invoke/Value/SelectionItem/Toggle use accessibility/UIA patterns.
5. Every mutation captures pre-action evidence before execution.
6. Successful mutations capture post-action evidence and deterministic visual
   comparison.
7. Incomplete post-action verification is recorded and surfaced as partial/
   unverified, never successful.
8. Values supplied to `set_value` are not persisted verbatim.
9. Deterministic CI uses fake providers and does not require Windows, live PCS or
   a paid model.
10. WP14-WP17 regressions and the full SceneWorks qualification remain green.
