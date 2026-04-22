#!/usr/bin/env bash
# Create /opt/ezra-runtime layout and seed env from repo examples (no secrets).
# Run as root once on a fresh host before install_or_update_services.sh.
set -euo pipefail

PUBLIC_DIR="${PUBLIC_DIR:-/opt/ezra-public}"
RUNTIME_ROOT="${RUNTIME_ROOT:-/opt/ezra-runtime}"
EXAMPLES="${PUBLIC_DIR}/trading-ai/deploy/env"

if [[ ! -d "${PUBLIC_DIR}/trading-ai/deploy/env" ]]; then
  echo "ERROR: missing ${PUBLIC_DIR}/trading-ai (clone or rsync public repo first)" >&2
  exit 2
fi

mkdir -p "${RUNTIME_ROOT}/env" "${RUNTIME_ROOT}/data/control" "${RUNTIME_ROOT}/databank" \
  "${RUNTIME_ROOT}/logs" "${RUNTIME_ROOT}/state"

if [[ ! -f "${RUNTIME_ROOT}/env/common.env" ]]; then
  install -m 0644 "${EXAMPLES}/common.env.example" "${RUNTIME_ROOT}/env/common.env"
  echo "OK: created ${RUNTIME_ROOT}/env/common.env from example"
else
  echo "OK: ${RUNTIME_ROOT}/env/common.env already exists (unchanged)"
fi

for optional in ops.env research.env; do
  ex="${EXAMPLES}/${optional}.example"
  dst="${RUNTIME_ROOT}/env/${optional}"
  if [[ -f "${ex}" && ! -f "${dst}" ]]; then
    install -m 0644 "${ex}" "${dst}"
    echo "OK: created ${dst}"
  fi
done

echo "NOTE: Live overlays are optional; copy *-live.env.example only when operator-ready (chmod 600)."
