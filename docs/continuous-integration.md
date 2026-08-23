# Continuous integration and release gate (WP3)

SceneWorks uses GitHub Actions as a deterministic barrier between a proposed SceneWorks change and `master`.

The workflow is `.github/workflows/ci.yml` and runs on every pull request and on pushes to `master`.

## Required jobs

### Backend tests

- Python 3.12 on Ubuntu.
- Dependency installation from the committed `uv.lock` using `uv sync --frozen`.
- `uv run pytest -m "not live"`.

Live-provider tests are intentionally excluded from the deterministic CI gate: availability of Gemini, OpenHands, credentials, or a model must never decide whether the repository's own deterministic tests pass.

### Deterministic qualification

Runs the complete provider-independent 19-scenario suite:

```bash
uv run python -m evaluation --json qualification.json
```

The full suite is used because SceneWorks deliberately reports partial deterministic runs such as `--smoke` as `BLOCKED`; a release gate must not treat “not all required scenarios ran” as success.

The machine-readable report is uploaded as a CI artifact even when the job fails.

### Frontend build

- Node.js 20.
- `npm ci` from the committed lock file.
- `npm run build`, which performs the production Next.js build and TypeScript validation.

## What CI does not claim

CI proves the deterministic SceneWorks contracts exercised by the repository tests and qualification suite. It does not prove that a particular live model will solve an arbitrary engineering task well. Live-provider qualification remains a separate, attributable evidence tier.

## Repository policy

The workflow supplies the status checks needed for branch protection. The repository should require the three CI jobs before merging changes to `master`; configuring the GitHub branch-protection/ruleset is an administrative repository setting rather than application code.
