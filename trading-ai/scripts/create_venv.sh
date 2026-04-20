#!/usr/bin/env bash
# Create venv from current python and install trading-ai editable + dev extras.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -d venv ]]; then
  python -m venv venv
fi
# shellcheck source=/dev/null
source venv/bin/activate
python -m pip install -U pip setuptools wheel
pip install -e ".[dev]"
echo "==> Installed editable package. python: $(which python)"
