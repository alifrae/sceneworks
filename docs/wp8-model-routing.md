# WP8 — Provider-neutral model profile routing

## Goal

`RoleDefinition.model_profile` is now executable configuration rather than
guidance-only metadata. Roles continue to express provider-neutral intent
(`strongest`, `coding`, `research`); SceneWorks resolves that intent to a
concrete backend/model when an `Execution` is created.

## Contract

1. A role supplies `backend` and optional `model_profile`.
2. `Settings.model_profile_routes` may override the backend and/or model for a
   profile.
3. If a profile has no explicit model, the selected backend's configured
   default model is used.
4. The resolved `backend`, `model_profile`, and `model_name` are persisted on
   the `Execution` **before it is queued**.
5. The execution engine transports that persisted model through `AgentRequest`.
6. `BackendRegistry` binds the model to an execution-scoped provider instance.
7. Later settings changes never rewrite or re-resolve an existing execution.

This is intentionally provider-neutral. Workflow, role, and graph code contain
no concrete Gemini/OpenHands model identifiers.

## Configuration

Environment/.env JSON:

```text
SCENEWORKS_MODEL_PROFILE_ROUTES={"strongest":{"backend":"gemini_acp","model":"<strong-model>"},"coding":{"backend":"gemini_acp","model":"<coding-model>"}}
```

The same mapping is available through `GET/PATCH /api/settings` under
`model_profile_routes`.

Each route supports:

- `backend`: optional registered backend key. Omit it to keep the role backend.
- `model`: optional concrete provider model string. Omit it to use the selected
  backend's configured default.

An unknown backend is rejected during execution creation, before an agent can
start.

## Persistence and migration

Migration `0005_execution_model_name.py` adds nullable
`executions.model_name`. Existing historical rows stay `NULL`; their actual
historical model cannot be reconstructed reliably and is not fabricated.

New execution API responses expose both:

- `model_profile`: provider-neutral intent.
- `model_name`: concrete immutable selection.

`execution.started` events also record `backend`, `model_profile`, and `model`.

## Provider binding

Gemini ACP receives a per-execution settings copy with `GEMINI_MODEL` pinned to
the persisted model. OpenHands receives an execution-scoped adapter whose
model resolver returns the persisted model even if the process environment has
a different default. This avoids mutable global environment changes and is safe
for concurrent executions using different models.

Fake/test backends receive `AgentRequest.model` but may ignore it.

## Invariants

- Profiles express intent; provider model names stay in configuration.
- Model/backend resolution happens exactly once per execution.
- Queued and restarted executions cannot drift after settings changes.
- Cancellation targets the same provider instance that is running the request.
- An unregistered routed backend fails closed.
- Existing behavior remains unchanged when `model_profile_routes` is empty.

## Verification

`tests/test_model_routing.py` covers:

- explicit profile backend/model resolution;
- backend-default fallback;
- invalid backend rejection;
- Gemini and OpenHands execution-scoped binding;
- persisted resolution immutability;
- API visibility and settings round-trip;
- engine transport of the persisted model and start-event evidence.

The normal deterministic qualification, complete non-live backend suite,
migration tests, and frontend production build remain the merge barrier.
