#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="${ROOT}/src${PYTHONPATH:+:$PYTHONPATH}"
: "${EZRAS_RUNTIME_ROOT:?Set EZRAS_RUNTIME_ROOT}"
python -m trading_ai.runtime role-lock-smoke --role ops
python -m trading_ai.runtime daemon --role ops --interval-sec 0.2 --cycles 2 --force-all-due
