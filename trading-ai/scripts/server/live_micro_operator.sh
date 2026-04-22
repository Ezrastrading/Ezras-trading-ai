#!/usr/bin/env bash
# Thin wrapper: exact paths for /opt server layout. No secrets embedded.
set -euo pipefail

PUBLIC_DIR="${PUBLIC_DIR:-/opt/ezra-public}"
PRIVATE_DIR="${PRIVATE_DIR:-/opt/ezra-private}"
RUNTIME_ROOT="${RUNTIME_ROOT:-/opt/ezra-runtime}"
VENV="${VENV:-/opt/ezra-venv}"
REPO="${PUBLIC_DIR}/trading-ai"
PY="${VENV}/bin/python"
export EZRAS_RUNTIME_ROOT="${RUNTIME_ROOT}"

usage() {
  cat <<'EOF'
Usage: live_micro_operator.sh <command>

  install              Run install_or_update_services.sh (sudo)
  enable-artifacts     live-micro-enablement-request + write-session-limits (env must be set)
  preflight            live-micro-preflight
  readiness            live-micro-readiness
  guard-proof          live-micro-guard-proof
  verify-contract      live-micro-verify-contract
  record-start         live-micro-record-start (COMPONENT/NOTE via env)
  pause                live-micro-pause
  resume               live-micro-resume
  disable-receipt      live-micro-disable-receipt (REASON env required)
  daemon-once          avenue-a-daemon-once (QUOTE_USD PRODUCT_ID optional env)
  list-proofs          ls data/control live_*.json

Env:
  RUNTIME_ROOT, PUBLIC_DIR, VENV
  COMPONENT (default operator_cli), DETAIL_JSON, OPERATOR, NOTE, REASON
  QUOTE_USD (default 1), PRODUCT_ID (default BTC-USD)
EOF
}

require_repo() {
  [[ -d "${REPO}" ]] || { echo "ERROR: missing ${REPO}" >&2; exit 2; }
  [[ -x "${PY}" ]] || { echo "ERROR: missing ${PY}" >&2; exit 2; }
}

cd_repo() {
  require_repo
  cd "${REPO}"
}

cmd="${1:-}"
shift || true
case "${cmd}" in
  ""|-h|--help) usage; exit 0 ;;
  install)
    sudo "${REPO}/scripts/server/install_or_update_services.sh" "$@"
    ;;
  enable-artifacts)
    cd_repo
    "${PY}" -m trading_ai.deployment live-micro-enablement-request \
      --operator "${OPERATOR:-$(whoami)}" --note "${NOTE:-enable-artifacts}" "$@"
    "${PY}" -m trading_ai.deployment live-micro-write-session-limits "$@"
    ;;
  preflight)
    cd_repo
    "${PY}" -m trading_ai.deployment live-micro-preflight "$@"
    ;;
  readiness)
    cd_repo
    "${PY}" -m trading_ai.deployment live-micro-readiness "$@"
    ;;
  guard-proof)
    cd_repo
    "${PY}" -m trading_ai.deployment live-micro-guard-proof "$@"
    ;;
  verify-contract)
    cd_repo
    "${PY}" -m trading_ai.deployment live-micro-verify-contract "$@"
    ;;
  record-start)
    cd_repo
    "${PY}" -m trading_ai.deployment live-micro-record-start \
      --component "${COMPONENT:-operator_cli}" \
      ${DETAIL_JSON:+--detail-json "${DETAIL_JSON}"} "$@"
    ;;
  pause)
    cd_repo
    "${PY}" -m trading_ai.deployment live-micro-pause \
      --operator "${OPERATOR:-$(whoami)}" --reason "${REASON:-operator_pause}" "$@"
    ;;
  resume)
    cd_repo
    "${PY}" -m trading_ai.deployment live-micro-resume "$@"
    ;;
  disable-receipt)
    cd_repo
    [[ -n "${REASON:-}" ]] || { echo "ERROR: set REASON=..." >&2; exit 2; }
    "${PY}" -m trading_ai.deployment live-micro-disable-receipt \
      --reason "${REASON}" --operator "${OPERATOR:-$(whoami)}" "$@"
    ;;
  daemon-once)
    cd_repo
    "${PY}" -m trading_ai.deployment avenue-a-daemon-once \
      --quote-usd "${QUOTE_USD:-1}" --product-id "${PRODUCT_ID:-BTC-USD}" "$@"
    ;;
  list-proofs)
    ls -la "${RUNTIME_ROOT}/data/control/live_"*.json 2>/dev/null || true
    ls -la "${RUNTIME_ROOT}/data/control/live_micro_"*.json 2>/dev/null || true
    ;;
  *)
    echo "ERROR: unknown command: ${cmd}" >&2
    usage
    exit 2
    ;;
esac
