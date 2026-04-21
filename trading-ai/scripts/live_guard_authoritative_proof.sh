#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="${ROOT}/src${PYTHONPATH:+:$PYTHONPATH}"
: "${EZRAS_RUNTIME_ROOT:?Set EZRAS_RUNTIME_ROOT}"
exec python -m trading_ai.runtime live-guard-proof
