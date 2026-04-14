#!/usr/bin/env bash
# Run trading-ai CLI from a git checkout without requiring PYTHONPATH.
# Usage: ./scripts/run_trading_ai.sh truth-sync status
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="${ROOT}/src${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m trading_ai "$@"
