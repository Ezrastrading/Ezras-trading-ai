#!/usr/bin/env bash
# Versioned wrapper (in-repo). Resolves workspace root: parent of `trading-ai/`
# (where gptr-venv and gpt-researcher-repo live alongside this repo).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
VENV_PYTHON="${ROOT}/gptr-venv/bin/python"
REPO="${ROOT}/gpt-researcher-repo"
CLI="${REPO}/cli.py"
TRADING_ENV="$(cd "$SCRIPT_DIR/.." && pwd)/.env"
REPO_ENV="${REPO}/.env"

if [[ ! -x "$VENV_PYTHON" ]]; then
  echo "gptr-run.sh: missing Python at $VENV_PYTHON" >&2
  exit 1
fi

if [[ ! -f "$CLI" ]]; then
  echo "gptr-run.sh: missing CLI at $CLI" >&2
  exit 1
fi

if [[ -f "$TRADING_ENV" ]]; then
  ln -sf "$TRADING_ENV" "$REPO_ENV"
fi

cd "$REPO"
export PYTHONPATH="$REPO"

exec "$VENV_PYTHON" "$CLI" "$@" \
  --report_type research_report \
  --tone objective \
  --no-pdf \
  --no-docx
