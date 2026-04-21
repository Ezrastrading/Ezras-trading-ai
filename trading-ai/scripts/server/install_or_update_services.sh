#!/usr/bin/env bash
set -euo pipefail

# Idempotent systemd installer/updater for Ezra services.
# Safe-by-default: never enables live trading; does not modify secrets.

PUBLIC_DIR="${PUBLIC_DIR:-/opt/ezra-public}"
PRIVATE_DIR="${PRIVATE_DIR:-/opt/ezra-private}"
RUNTIME_ROOT="${RUNTIME_ROOT:-/opt/ezra-runtime}"
VENV="${VENV:-/opt/ezra-venv}"

UNIT_SRC_DIR="${UNIT_SRC_DIR:-${PUBLIC_DIR}/trading-ai/docs/systemd}"
UNIT_DST_DIR="${UNIT_DST_DIR:-/etc/systemd/system}"

require_root() {
  if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    echo "ERROR: must run as root (sudo) to install systemd units into ${UNIT_DST_DIR}" >&2
    exit 2
  fi
}

verify_paths() {
  [[ -d "${PUBLIC_DIR}/trading-ai" ]] || { echo "ERROR: missing ${PUBLIC_DIR}/trading-ai" >&2; exit 2; }
  [[ -d "${PRIVATE_DIR}/trading-ai" ]] || { echo "ERROR: missing ${PRIVATE_DIR}/trading-ai" >&2; exit 2; }
  [[ -x "${VENV}/bin/python" ]] || { echo "ERROR: missing venv python at ${VENV}/bin/python" >&2; exit 2; }
  [[ -d "${RUNTIME_ROOT}" ]] || { echo "ERROR: missing runtime root ${RUNTIME_ROOT}" >&2; exit 2; }
  [[ -d "${RUNTIME_ROOT}/env" ]] || { echo "ERROR: missing ${RUNTIME_ROOT}/env" >&2; exit 2; }
  [[ -f "${RUNTIME_ROOT}/env/common.env" ]] || { echo "ERROR: missing ${RUNTIME_ROOT}/env/common.env" >&2; exit 2; }
  [[ -d "${UNIT_SRC_DIR}" ]] || { echo "ERROR: missing unit source dir ${UNIT_SRC_DIR}" >&2; exit 2; }
  [[ -f "${UNIT_SRC_DIR}/ezra-ops.service" ]] || { echo "ERROR: missing ${UNIT_SRC_DIR}/ezra-ops.service" >&2; exit 2; }
  [[ -f "${UNIT_SRC_DIR}/ezra-research.service" ]] || { echo "ERROR: missing ${UNIT_SRC_DIR}/ezra-research.service" >&2; exit 2; }
}

install_units() {
  install -m 0644 "${UNIT_SRC_DIR}/ezra-ops.service" "${UNIT_DST_DIR}/ezra-ops.service"
  install -m 0644 "${UNIT_SRC_DIR}/ezra-research.service" "${UNIT_DST_DIR}/ezra-research.service"
}

reload_enable() {
  systemctl daemon-reload
  systemctl enable ezra-ops.service
  systemctl enable ezra-research.service
}

status_summary() {
  systemctl --no-pager --full status ezra-ops.service || true
  systemctl --no-pager --full status ezra-research.service || true
}

main() {
  require_root
  verify_paths
  install_units
  reload_enable
  echo "OK: installed/updated units in ${UNIT_DST_DIR}"
  echo "NOTE: services are enabled but not restarted by this script."
  echo "Run: systemctl restart ezra-ops.service ezra-research.service"
  status_summary
}

main "$@"

