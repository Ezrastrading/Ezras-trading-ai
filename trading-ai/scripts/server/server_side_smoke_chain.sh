#!/usr/bin/env bash
# End-to-end smoke chain: preflight → deployed smoke → micro readiness → full autonomy smoke.
# Safe-by-default: does not enable live trading. Intended for /opt layout or dev clone with overrides.
set -euo pipefail

PUBLIC_DIR="${PUBLIC_DIR:-/opt/ezra-public}"
PRIVATE_DIR="${PRIVATE_DIR:-/opt/ezra-private}"
RUNTIME_ROOT="${RUNTIME_ROOT:-/opt/ezra-runtime}"
VENV="${VENV:-/opt/ezra-venv}"
PY="${VENV}/bin/python"
export PYTHONPATH="${PRIVATE_DIR}/trading-ai/src:${PUBLIC_DIR}/trading-ai/src"
export EZRAS_RUNTIME_ROOT="${RUNTIME_ROOT}"
export NTE_EXECUTION_MODE="${NTE_EXECUTION_MODE:-paper}"
export NTE_LIVE_TRADING_ENABLED="${NTE_LIVE_TRADING_ENABLED:-false}"
export COINBASE_EXECUTION_ENABLED="${COINBASE_EXECUTION_ENABLED:-false}"

if [[ ! -x "${PY}" ]]; then
  echo "ERROR: venv python missing: ${PY}" >&2
  exit 2
fi

ROOT="${PUBLIC_DIR}/trading-ai"
if [[ ! -d "${ROOT}" ]]; then
  echo "ERROR: missing ${ROOT}" >&2
  exit 2
fi

echo "== 1) deploy_preflight"
"${PY}" "${ROOT}/scripts/server/deploy_preflight.py" \
  --public-root "${PUBLIC_DIR}" --private-root "${PRIVATE_DIR}" \
  --runtime-root "${RUNTIME_ROOT}" --venv-root "${VENV}" --write-report

echo "== 2) deployed_environment_smoke"
"${PY}" "${ROOT}/scripts/server/deployed_environment_smoke.py" \
  --public-root "${PUBLIC_DIR}" --private-root "${PRIVATE_DIR}" \
  --runtime-root "${RUNTIME_ROOT}" --venv-root "${VENV}"

echo "== 3) micro_trade_readiness"
"${PY}" "${ROOT}/scripts/server/micro_trade_readiness.py" \
  --runtime-root "${RUNTIME_ROOT}" --public-root "${PUBLIC_DIR}" --private-root "${PRIVATE_DIR}"

echo "== 4) full_autonomy_smoke (uses its own temp root unless EZRA_SMOKE_REUSE_RUNTIME=1)"
if [[ "${EZRA_SMOKE_REUSE_RUNTIME:-0}" == "1" ]]; then
  export PYTHONPATH="${PUBLIC_DIR}/trading-ai/src:${PYTHONPATH}"
  "${PY}" "${ROOT}/scripts/server/full_autonomy_smoke.py" --ticks 10 --runtime-root "${RUNTIME_ROOT}"
else
  export PYTHONPATH="${PUBLIC_DIR}/trading-ai/src:${PYTHONPATH}"
  "${PY}" "${ROOT}/scripts/server/full_autonomy_smoke.py" --ticks 10
fi

echo "== 5) production_stack_proof (systemd + smokes on RUNTIME_ROOT; merge env if live overlays exist)"
"${PY}" "${ROOT}/scripts/server/production_stack_proof.py" \
  --public-root "${PUBLIC_DIR}" \
  --private-root "${PRIVATE_DIR}" \
  --runtime-root "${RUNTIME_ROOT}" \
  --venv-root "${VENV}" \
  --merge-runtime-env-files

echo "OK: server_side_smoke_chain completed"
