#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="${ROOT}/src${PYTHONPATH:+:$PYTHONPATH}"
: "${EZRAS_RUNTIME_ROOT:?Set EZRAS_RUNTIME_ROOT to a writable runtime directory}"
python -m trading_ai.runtime write-now-live-artifacts
python -m trading_ai.runtime live-guard-proof
python -m trading_ai.runtime supervisor-once --role ops --force-all-due --skip-models
python -m trading_ai.runtime supervisor-once --role research --force-all-due --skip-models
python -m trading_ai.runtime accelerated-sim --cycles 8 --skip-models
python -m trading_ai.runtime autonomy-deploy-preflight
python -m trading_ai.runtime systemd-unit-contract-verify
