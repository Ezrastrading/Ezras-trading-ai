#!/usr/bin/env bash
set -euo pipefail

# Dual-repo deployment script (fail-closed).
#
# Layout:
# - PUBLIC_DIR  (default /opt/ezra-public)  contains public repo checkout at PUBLIC_DIR/trading-ai
# - PRIVATE_DIR (default /opt/ezra-private) contains private repo checkout at PRIVATE_DIR/trading-ai
# - RUNTIME_ROOT (default /opt/ezra-runtime) holds runtime artifacts + env files
# - VENV (default /opt/ezra-venv) python venv used by services
#
# Contract:
# - Never enables live trading
# - Only restarts services if preflight + smoke pass

PUBLIC_DIR="${PUBLIC_DIR:-/opt/ezra-public}"
PRIVATE_DIR="${PRIVATE_DIR:-/opt/ezra-private}"
RUNTIME_ROOT="${RUNTIME_ROOT:-/opt/ezra-runtime}"
VENV="${VENV:-/opt/ezra-venv}"

PUBLIC_REF="${PUBLIC_REF:-}"
PRIVATE_REF="${PRIVATE_REF:-}"

need_root() {
  if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    echo "ERROR: must run as root (sudo) to manage systemd + repos" >&2
    exit 2
  fi
}

require_paths() {
  [[ -d "${PUBLIC_DIR}/trading-ai/.git" ]] || { echo "ERROR: missing git repo at ${PUBLIC_DIR}/trading-ai" >&2; exit 2; }
  [[ -d "${PRIVATE_DIR}/trading-ai/.git" ]] || { echo "ERROR: missing git repo at ${PRIVATE_DIR}/trading-ai" >&2; exit 2; }
  [[ -x "${VENV}/bin/python" ]] || { echo "ERROR: missing venv python ${VENV}/bin/python" >&2; exit 2; }
  [[ -d "${RUNTIME_ROOT}/env" ]] || { echo "ERROR: missing ${RUNTIME_ROOT}/env" >&2; exit 2; }
  [[ -f "${RUNTIME_ROOT}/env/common.env" ]] || { echo "ERROR: missing ${RUNTIME_ROOT}/env/common.env" >&2; exit 2; }
}

checkout_ref() {
  local repo="$1"
  local ref="$2"
  if [[ -z "${ref}" ]]; then
    return 0
  fi
  (cd "${repo}" && git fetch --all --tags && git checkout --quiet "${ref}")
}

write_refs() {
  local out="${RUNTIME_ROOT}/data/control/deployed_refs.json"
  mkdir -p "$(dirname "${out}")"
  local pub_sha priv_sha
  pub_sha="$(cd "${PUBLIC_DIR}/trading-ai" && git rev-parse HEAD)"
  priv_sha="$(cd "${PRIVATE_DIR}/trading-ai" && git rev-parse HEAD)"
  cat > "${out}" <<EOF
{
  "truth_version": "deployed_refs_v1",
  "public_sha": "${pub_sha}",
  "private_sha": "${priv_sha}"
}
EOF
  echo "OK: wrote ${out}"
}

run_preflight() {
  # shellcheck disable=SC1090
  source "${RUNTIME_ROOT}/env/common.env"
  PYTHONPATH="${PRIVATE_DIR}/trading-ai/src:${PUBLIC_DIR}/trading-ai/src" \
    "${VENV}/bin/python" "${PUBLIC_DIR}/trading-ai/scripts/server/deploy_preflight.py" \
      --public-root "${PUBLIC_DIR}" \
      --private-root "${PRIVATE_DIR}" \
      --runtime-root "${RUNTIME_ROOT}" \
      --venv-root "${VENV}" \
      --write-report
}

run_smoke() {
  PYTHONPATH="${PRIVATE_DIR}/trading-ai/src:${PUBLIC_DIR}/trading-ai/src" \
    "${VENV}/bin/python" "${PUBLIC_DIR}/trading-ai/scripts/server/deployed_environment_smoke.py" \
      --public-root "${PUBLIC_DIR}" \
      --private-root "${PRIVATE_DIR}" \
      --runtime-root "${RUNTIME_ROOT}" \
      --venv-root "${VENV}"
}

restart_services() {
  systemctl restart ezra-ops.service ezra-research.service
  systemctl --no-pager --full status ezra-ops.service || true
  systemctl --no-pager --full status ezra-research.service || true
}

main() {
  need_root
  require_paths

  echo "==> Checking out refs (if provided)"
  checkout_ref "${PUBLIC_DIR}/trading-ai" "${PUBLIC_REF}"
  checkout_ref "${PRIVATE_DIR}/trading-ai" "${PRIVATE_REF}"

  echo "==> Running deploy preflight (fail-closed)"
  run_preflight

  echo "==> Running deployed environment smoke (fail-closed)"
  run_smoke

  echo "==> Writing deployed refs"
  write_refs

  echo "==> Restarting services (only after proofs pass)"
  restart_services

  echo "OK: deploy complete (non-live posture maintained)"
}

main "$@"

