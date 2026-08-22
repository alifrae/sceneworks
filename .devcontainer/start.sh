#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
RUNTIME_DIR="/tmp/sceneworks-codespaces"
export PATH="$HOME/.local/bin:$PATH"

mkdir -p "$RUNTIME_DIR" /workspaces/sceneworks-worktrees

if ! curl -fsS http://127.0.0.1:8010/api/health >/dev/null 2>&1; then
  echo "[SceneWorks] Starting FastAPI on localhost:8010..."
  (
    cd "$ROOT/backend"
    nohup env \
      SCENEWORKS_HOST=127.0.0.1 \
      SCENEWORKS_PORT=8010 \
      SCENEWORKS_WORKTREE_ROOT=/workspaces/sceneworks-worktrees \
      uv run uvicorn app.main:app --host 127.0.0.1 --port 8010 \
      >"$RUNTIME_DIR/backend.log" 2>&1 &
    echo $! >"$RUNTIME_DIR/backend.pid"
  )
fi

for _ in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8010/api/health >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if ! curl -fsS http://127.0.0.1:8010/api/health >/dev/null 2>&1; then
  echo "[SceneWorks] Backend failed to start. See $RUNTIME_DIR/backend.log" >&2
  exit 1
fi

if ! curl -fsS http://127.0.0.1:3000 >/dev/null 2>&1; then
  echo "[SceneWorks] Starting Next.js on port 3000..."
  (
    cd "$ROOT/web"
    nohup env \
      NEXT_PUBLIC_API_URL="" \
      SCENEWORKS_INTERNAL_API_URL=http://127.0.0.1:8010 \
      npm run dev -- --hostname 0.0.0.0 --port 3000 \
      >"$RUNTIME_DIR/web.log" 2>&1 &
    echo $! >"$RUNTIME_DIR/web.pid"
  )
fi

echo "[SceneWorks] Ready."
echo "[SceneWorks] Open forwarded port 3000 and keep it Private."
echo "[SceneWorks] Backend remains bound to 127.0.0.1:8010."
echo "[SceneWorks] Logs: $RUNTIME_DIR/backend.log and $RUNTIME_DIR/web.log"
