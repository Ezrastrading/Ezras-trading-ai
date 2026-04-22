#!/usr/bin/env bash
# Operator: micro live + FULL_AUTONOMY_ACTIVE on a systemd host (/opt layout).
# See deploy/env/ops-live.micro.template and docs/LIVE_MICRO_OPERATOR_PACK.md.
set -euo pipefail

PUBLIC_ROOT="${PUBLIC_ROOT:-/opt/ezra-public}"
PRIVATE_ROOT="${PRIVATE_ROOT:-/opt/ezra-private}"
RUNROOT="${RUNTIME_ROOT:-/opt/ezra-runtime}"
VENV="${VENV:-/opt/ezra-venv}"
REPO="${PUBLIC_ROOT}/trading-ai"
PY="${VENV}/bin/python"

if [[ "${EUID:-0}" -ne 0 ]]; then
  echo "ERROR: run as root (systemd + install paths)." >&2
  exit 2
fi
if [[ ! -x "${PY}" ]]; then
  echo "ERROR: missing ${PY}" >&2
  exit 2
fi

export EZRAS_RUNTIME_ROOT="${RUNROOT}"
export PYTHONPATH="${PRIVATE_ROOT}/trading-ai/src:${PUBLIC_ROOT}/trading-ai/src"

echo "== 0) Refresh deployed_environment_smoke under forced paper (preflight consumes this JSON)"
(
  export EZRAS_RUNTIME_ROOT="${RUNROOT}"
  export PYTHONPATH="${PRIVATE_ROOT}/trading-ai/src:${PUBLIC_ROOT}/trading-ai/src"
  export NTE_EXECUTION_MODE=paper
  export NTE_LIVE_TRADING_ENABLED=false
  export COINBASE_EXECUTION_ENABLED=false
  export NTE_PAPER_MODE=true
  export NTE_DRY_RUN=true
  "${PY}" "${REPO}/scripts/server/deployed_environment_smoke.py" \
    --public-root "${PUBLIC_ROOT}" \
    --private-root "${PRIVATE_ROOT}" \
    --runtime-root "${RUNROOT}" \
    --venv-root "${VENV}"
  "${PY}" "${REPO}/scripts/server/micro_trade_readiness.py" \
    --runtime-root "${RUNROOT}" \
    --public-root "${PUBLIC_ROOT}" \
    --private-root "${PRIVATE_ROOT}"
)

echo "== 1) Install ops-live.env from template (micro starts disabled)"
install -m 0600 "${REPO}/deploy/env/ops-live.micro.template" "${RUNROOT}/env/ops-live.env"

echo "== 2) Persist FULL_AUTONOMY_ACTIVE artifacts (no shell env mutation)"
"${PY}" "${REPO}/scripts/server/enable_full_autonomy_active_live.py" \
  --runtime-root "${RUNROOT}" \
  --artifacts-only \
  --reason=activate_micro_live_stack

echo "== 3) Restart role daemons"
systemctl daemon-reload
systemctl restart ezra-ops.service ezra-research.service
sleep 4
systemctl is-active ezra-ops.service ezra-research.service

echo "== 4) Live micro proof chain (merged /opt env + optional secrets.env)"
set -a
# shellcheck disable=SC1090
[[ -f "${RUNROOT}/env/common.env" ]] && source "${RUNROOT}/env/common.env"
# shellcheck disable=SC1090
[[ -f "${RUNROOT}/env/ops.env" ]] && source "${RUNROOT}/env/ops.env"
# shellcheck disable=SC1090
[[ -f "${RUNROOT}/env/ops-live.env" ]] && source "${RUNROOT}/env/ops-live.env"
# shellcheck disable=SC1090
[[ -f "${RUNROOT}/env/secrets.env" ]] && source "${RUNROOT}/env/secrets.env"
set +a
export EZRAS_RUNTIME_ROOT="${RUNROOT}"
export PYTHONPATH="${PRIVATE_ROOT}/trading-ai/src:${PUBLIC_ROOT}/trading-ai/src"
cd "${REPO}"

"${PY}" -m trading_ai.deployment live-micro-resume --runtime-root "${RUNROOT}" >/dev/null 2>&1 || true
"${PY}" -m trading_ai.deployment live-micro-enablement-request \
  --runtime-root "${RUNROOT}" --operator "$(logname 2>/dev/null || echo root)" --note "activate_micro_live_stack"
"${PY}" -m trading_ai.deployment live-micro-write-session-limits --runtime-root "${RUNROOT}"
"${PY}" -m trading_ai.deployment live-micro-preflight --runtime-root "${RUNROOT}"
"${PY}" -m trading_ai.deployment live-micro-readiness --runtime-root "${RUNROOT}"
"${PY}" -m trading_ai.deployment live-micro-guard-proof --runtime-root "${RUNROOT}"

if command -v jq >/dev/null 2>&1; then
  if ! jq -e '.ok == true' "${RUNROOT}/data/control/live_preflight.json" >/dev/null; then
    echo "ERROR: live_preflight.json not ok — inspect blockers; leaving EZRA_LIVE_MICRO_ENABLED=false" >&2
    exit 3
  fi
  if ! jq -e '.ok == true' "${RUNROOT}/data/control/live_micro_readiness.json" >/dev/null; then
    echo "ERROR: live_micro_readiness.json not ok" >&2
    exit 4
  fi
else
  echo "WARN: jq missing; skipping strict JSON ok checks — review artifacts manually" >&2
fi

echo "== 5) Enable micro runtime flag in ops-live.env + restart"
sed -i 's/^EZRA_LIVE_MICRO_ENABLED=false/EZRA_LIVE_MICRO_ENABLED=true/' "${RUNROOT}/env/ops-live.env"
systemctl restart ezra-ops.service ezra-research.service
sleep 4
systemctl is-active ezra-ops.service ezra-research.service

echo "== 6) Production stack proof (merged runtime env)"
"${PY}" "${REPO}/scripts/server/production_stack_proof.py" \
  --public-root "${PUBLIC_ROOT}" \
  --private-root "${PRIVATE_ROOT}" \
  --runtime-root "${RUNROOT}" \
  --venv-root "${VENV}" \
  --merge-runtime-env-files

echo "OK: activate_micro_live_stack completed"
