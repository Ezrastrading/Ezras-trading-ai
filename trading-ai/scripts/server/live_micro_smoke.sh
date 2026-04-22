#!/usr/bin/env bash
# CI / operator smoke: micro-live module tests + CLI wiring (tmp runtime root).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "${ROOT}"
export PYTHONPATH="${ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"
PY="${PYTHON:-$(command -v python3.11 2>/dev/null || command -v python3)}"
TMP="$(mktemp -d)"
cleanup() { rm -rf "${TMP}"; }
trap cleanup EXIT

export EZRAS_RUNTIME_ROOT="${TMP}"
export EZRA_LIVE_MICRO_OPERATOR_CONFIRM="I_ACCEPT_MICRO_LIVE_CAPITAL_RISK_AND_LIMITS"
export EZRA_LIVE_MICRO_MAX_NOTIONAL_USD="2"
export EZRA_LIVE_MICRO_MAX_DAILY_LOSS_USD="10"
export EZRA_LIVE_MICRO_MAX_TOTAL_EXPOSURE_USD="5"
export EZRA_LIVE_MICRO_ALLOWED_PRODUCTS="BTC-USD"
export EZRA_LIVE_MICRO_ALLOWED_AVENUE="COINBASE"
export EZRA_LIVE_MICRO_ALLOWED_GATE="gate_a"
export EZRA_LIVE_MICRO_MAX_TRADES_PER_SESSION="2"
export EZRA_LIVE_MICRO_COOLDOWN_SEC="0"
export EZRA_LIVE_MICRO_MAX_CONCURRENT_POSITIONS="1"

"${PY}" -m pytest tests/test_live_micro_enablement.py -q

# CLI should accept --runtime-root and write request (will fail contract_ok without full env in other tools)
out="$("${PY}" -m trading_ai.deployment live-micro-enablement-request --runtime-root "${TMP}" --operator smoke --note cli 2>&1)" || true
echo "${out}" | head -20

"${PY}" -m trading_ai.deployment live-micro-resume --runtime-root "${TMP}" >/dev/null

echo "OK: live_micro_smoke"
