#!/usr/bin/env bash
set -euo pipefail

# Safe dual-repo deploy script for server.
# - fetches public + private repos
# - checks out explicit refs
# - writes deployed refs artifact
# - runs preflight + deployed smoke
# - restarts services only if both pass
#
# NEVER enables live trading. Fail-closed.

PUBLIC_DIR="${PUBLIC_DIR:-/opt/ezra-public}"
PRIVATE_DIR="${PRIVATE_DIR:-/opt/ezra-private}"
RUNTIME_ROOT="${RUNTIME_ROOT:-/opt/ezra-runtime}"
VENV="${VENV:-/opt/ezra-venv}"

PUBLIC_REF="${PUBLIC_REF:-}"
PRIVATE_REF="${PRIVATE_REF:-}"

require_tools() {
  command -v git >/dev/null || { echo "ERROR: git missing" >&2; exit 2; }
  [[ -x "${VENV}/bin/python" ]] || { echo "ERROR: venv python missing at ${VENV}/bin/python" >&2; exit 2; }
}

require_refs() {
  [[ -n "${PUBLIC_REF}" ]] || { echo "ERROR: PUBLIC_REF required (commit/tag/branch)" >&2; exit 2; }
  [[ -n "${PRIVATE_REF}" ]] || { echo "ERROR: PRIVATE_REF required (commit/tag/branch)" >&2; exit 2; }
}

git_fetch_checkout() {
  local dir="$1"
  local ref="$2"
  [[ -d "${dir}/.git" ]] || { echo "ERROR: ${dir} is not a git repo" >&2; exit 2; }
  git -C "${dir}" fetch --all --tags
  git -C "${dir}" checkout -f "${ref}"
  git -C "${dir}" submodule update --init --recursive || true
}

write_deployed_refs() {
  local pub_sha
  local pri_sha
  pub_sha="$(git -C "${PUBLIC_DIR}" rev-parse HEAD)"
  pri_sha="$(git -C "${PRIVATE_DIR}" rev-parse HEAD)"
  mkdir -p "${RUNTIME_ROOT}/data/control"
  cat > "${RUNTIME_ROOT}/data/control/deployed_refs.json" <<EOF
{
  "truth_version": "deployed_refs_v1",
  "generated_at_utc": "$(date -u +"%Y-%m-%dT%H:%M:%SZ")",
  "public": { "path": "${PUBLIC_DIR}", "ref": "${PUBLIC_REF}", "sha": "${pub_sha}" },
  "private": { "path": "${PRIVATE_DIR}", "ref": "${PRIVATE_REF}", "sha": "${pri_sha}" }
}
EOF
}

run_preflight() {
  "${VENV}/bin/python" "${PUBLIC_DIR}/trading-ai/scripts/server/deploy_preflight.py" \
    --public-root "${PUBLIC_DIR}" \
    --private-root "${PRIVATE_DIR}" \
    --runtime-root "${RUNTIME_ROOT}" \
    --venv-root "${VENV}" \
    --write-report
}

run_smoke() {
  "${VENV}/bin/python" "${PUBLIC_DIR}/trading-ai/scripts/server/deployed_environment_smoke.py" \
    --public-root "${PUBLIC_DIR}" \
    --private-root "${PRIVATE_DIR}" \
    --runtime-root "${RUNTIME_ROOT}" \
    --venv-root "${VENV}"
}

restart_services() {
  systemctl restart ezra-ops.service
  systemctl restart ezra-research.service
}

status_summary() {
  systemctl --no-pager --full status ezra-ops.service || true
  systemctl --no-pager --full status ezra-research.service || true
}

main() {
  require_tools
  require_refs

  echo "== Deploy: fetch + checkout public ${PUBLIC_REF}"
  git_fetch_checkout "${PUBLIC_DIR}" "${PUBLIC_REF}"

  echo "== Deploy: fetch + checkout private ${PRIVATE_REF}"
  git_fetch_checkout "${PRIVATE_DIR}" "${PRIVATE_REF}"

  write_deployed_refs

  echo "== Preflight (fail-closed)"
  run_preflight

  echo "== Deployed environment smoke (fail-closed)"
  run_smoke

  echo "== Restarting services (only after passing)"
  restart_services

  echo "== Status summary"
  status_summary
  echo "OK: deploy complete"
}

main "$@"

