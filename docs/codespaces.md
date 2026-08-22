# SceneWorks on GitHub Codespaces

SceneWorks can run in a GitHub Codespace so the control plane is usable from a browser, including a phone.

## Security model

Only the Next.js frontend on port 3000 is forwarded by Codespaces.

The FastAPI control plane stays bound to `127.0.0.1:8010`. Browser requests to `/api/*` go to Next.js first and are proxied internally to FastAPI. This preserves the existing SceneWorks trust assumption: the unauthenticated FastAPI service is not directly exposed as a forwarded port.

Keep port 3000 **Private**. GitHub Codespaces private ports require GitHub authentication and are accessible only to the codespace owner.

## Gemini authentication

For headless use, configure `GEMINI_API_KEY` as a GitHub Codespaces secret. Do not commit the key to this repository.

The devcontainer installs Gemini CLI automatically.

## Start

Create a Codespace for this repository. The devcontainer will:

1. install `uv`
2. install backend dependencies with `uv sync`
3. install frontend dependencies with `npm ci`
4. install Gemini CLI
5. start FastAPI on `127.0.0.1:8010`
6. start Next.js on `0.0.0.0:3000`
7. forward only port 3000

Open the forwarded **SceneWorks** port from the Codespaces Ports view.

On resume, `postStartCommand` starts the services again if they are not already running.

## Managed repositories

SceneWorks agents require the managed repository to exist inside the same Codespace filesystem.

Clone a repository into `/workspaces`, for example:

```bash
cd /workspaces
git clone https://github.com/OWNER/REPOSITORY.git
```

Then add it in SceneWorks using its absolute path:

```text
/workspaces/REPOSITORY
```

Agent worktrees are created under:

```text
/workspaces/sceneworks-worktrees
```

This is deliberately outside the SceneWorks repository and outside managed repositories.

## Diagnostics

```bash
curl http://127.0.0.1:8010/api/health
tail -f /tmp/sceneworks-codespaces/backend.log
tail -f /tmp/sceneworks-codespaces/web.log
gemini --version
```

Deleting the Codespace deletes its local SceneWorks database, cloned repositories, and other Codespace-local state. Push important Git changes before deleting it.
