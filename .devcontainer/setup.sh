#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
export PATH="$HOME/.local/bin:$PATH"

if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi

export PATH="$HOME/.local/bin:$PATH"

echo "[SceneWorks] Installing backend dependencies..."
(
  cd "$ROOT/backend"
  uv sync
)

echo "[SceneWorks] Installing frontend dependencies..."
(
  cd "$ROOT/web"
  npm ci
)

echo "[SceneWorks] Installing Gemini CLI..."
npm install -g @google/gemini-cli

mkdir -p /workspaces/sceneworks-worktrees

if [[ -z "${GEMINI_API_KEY:-}" ]]; then
  echo "[SceneWorks] GEMINI_API_KEY is not set."
  echo "[SceneWorks] Add it as a GitHub Codespaces secret for headless Gemini authentication."
fi
