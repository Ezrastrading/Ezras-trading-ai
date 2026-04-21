#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="${ROOT}/src${PYTHONPATH:+:$PYTHONPATH}"
: "${EZRAS_RUNTIME_ROOT:?Set EZRAS_RUNTIME_ROOT}"
CYCLES="${1:-12}"
exec python -m trading_ai.runtime accelerated-sim --cycles "$CYCLES" --skip-models
