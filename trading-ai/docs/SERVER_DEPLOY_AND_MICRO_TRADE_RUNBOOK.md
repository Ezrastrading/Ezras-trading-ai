# Server deploy, verification, and first micro-trades (runbook)

Canonical layout (see also `docs/DEPLOYMENT_BOOT_PROOF.md`):

| Path | Role |
|------|------|
| `/opt/ezra-public` | Public git repo root (contains `trading-ai/`) |
| `/opt/ezra-private` | Private git repo root (contains `trading-ai/`) |
| `/opt/ezra-runtime` | `EZRAS_RUNTIME_ROOT` — writable state, `env/`, `data/control/` |
| `/opt/ezra-venv` | Python venv (`bin/python`) |

**PYTHONPATH** (private wins):  
`/opt/ezra-private/trading-ai/src:/opt/ezra-public/trading-ai/src`

**Known-good commit (this runbook written for):** `e6d3bf1` on `main` — replace `PUBLIC_REF` / `PRIVATE_REF` with your verified SHAs or tags.

---

## A) One-shot server deploy command pack (ordered, copy-paste)

Run as a user with `sudo` where indicated. Replace refs as needed.

```bash
# --- 0) Variables (edit) ---
export PUBLIC_REF="e6d3bf1"
export PRIVATE_REF="e6d3bf1"
export PUBLIC_DIR="/opt/ezra-public"
export PRIVATE_DIR="/opt/ezra-private"
export RUNTIME_ROOT="/opt/ezra-runtime"
export VENV="/opt/ezra-venv"
export PY="${VENV}/bin/python"
export PP="/opt/ezra-private/trading-ai/src:/opt/ezra-public/trading-ai/src"

# --- 1) Pull + checkout both repos + preflight + smoke + restart (atomic gate) ---
sudo PUBLIC_REF="${PUBLIC_REF}" PRIVATE_REF="${PRIVATE_REF}" \
  PUBLIC_DIR="${PUBLIC_DIR}" PRIVATE_DIR="${PRIVATE_DIR}" \
  RUNTIME_ROOT="${RUNTIME_ROOT}" VENV="${VENV}" \
  "${PUBLIC_DIR}/trading-ai/scripts/server/deploy_dual_repo.sh"

# --- 2) Service status (human) ---
sudo systemctl --no-pager --full status ezra-ops.service
sudo systemctl --no-pager --full status ezra-research.service

# --- 3) Journals (last 200 lines) ---
sudo journalctl -u ezra-ops.service -n 200 --no-pager
sudo journalctl -u ezra-research.service -n 200 --no-pager

# --- 4) Live flags still non-live (from unit drop-ins / env files) ---
systemctl show -p Environment ezra-ops.service | tr ' ' '\n' | egrep 'NTE_EXECUTION_MODE|NTE_LIVE_TRADING_ENABLED|COINBASE_EXECUTION_ENABLED|EZRAS_RUNTIME_ROOT' || true
systemctl show -p Environment ezra-research.service | tr ' ' '\n' | egrep 'NTE_EXECUTION_MODE|NTE_LIVE_TRADING_ENABLED|COINBASE_EXECUTION_ENABLED|EZRAS_RUNTIME_ROOT' || true

# --- 5) Re-run preflight + deployed smoke (same as services) ---
source "${RUNTIME_ROOT}/env/common.env"
export PYTHONPATH="${PP}"
export EZRAS_RUNTIME_ROOT="${RUNTIME_ROOT}"
"${PY}" "${PUBLIC_DIR}/trading-ai/scripts/server/deploy_preflight.py" \
  --public-root "${PUBLIC_DIR}" --private-root "${PRIVATE_DIR}" \
  --runtime-root "${RUNTIME_ROOT}" --venv-root "${VENV}" --write-report
"${PY}" "${PUBLIC_DIR}/trading-ai/scripts/server/deployed_environment_smoke.py" \
  --public-root "${PUBLIC_DIR}" --private-root "${PRIVATE_DIR}" \
  --runtime-root "${RUNTIME_ROOT}" --venv-root "${VENV}"

# --- 6) Micro-trade readiness gate ---
"${PY}" "${PUBLIC_DIR}/trading-ai/scripts/server/micro_trade_readiness.py" \
  --runtime-root "${RUNTIME_ROOT}" --public-root "${PUBLIC_DIR}" --private-root "${PRIVATE_DIR}"

# --- 7) End-to-end local-style proof on server (optional temp root; pass --runtime-root to reuse) ---
export PYTHONPATH="${PUBLIC_DIR}/trading-ai/src:${PYTHONPATH}"
"${PY}" "${PUBLIC_DIR}/trading-ai/scripts/server/full_autonomy_smoke.py" --ticks 10
```

**First install (no `deploy_dual_repo.sh` yet):** install units, create `env/common.env`, then run section 5–7.

```bash
sudo PUBLIC_DIR=/opt/ezra-public PRIVATE_DIR=/opt/ezra-private \
  RUNTIME_ROOT=/opt/ezra-runtime VENV=/opt/ezra-venv \
  /opt/ezra-public/trading-ai/scripts/server/install_or_update_services.sh
sudo systemctl restart ezra-ops.service ezra-research.service
```

---

## B) Proof artifacts to inspect on the server

| Artifact | What it proves |
|----------|----------------|
| `${RUNTIME_ROOT}/data/control/deployed_refs.json` | Public/private SHAs after `deploy_dual_repo.sh` |
| `${RUNTIME_ROOT}/data/control/deploy_preflight.json` | Filesystem, venv python, live-disabled, critical imports |
| `${RUNTIME_ROOT}/data/control/deployed_environment_smoke.json` | Imports, `EZRAS_RUNTIME_ROOT` wiring, ops/research supervisor tick, loop/mission JSON, optional sim/router file map, git heads |
| `${RUNTIME_ROOT}/data/control/micro_trade_readiness.json` | Gate B: artifacts + inbox + databank + post-trade/first-twenty import paths + freshness |
| `${RUNTIME_ROOT}/data/control/operating_system/loop_status_ops.json` | Ops supervisor |
| `${RUNTIME_ROOT}/data/control/operating_system/loop_status_research.json` | Research supervisor |
| `${RUNTIME_ROOT}/data/control/mission_goals_operating_plan.json` | Mission / goals path |
| `${RUNTIME_ROOT}/data/control/bot_inboxes/*.json` | Task routing / intake |

Simulation bundle (after `full_autonomy_smoke` or `run_sim_24h.py`): `sim_*`, `regression_drift.json`, `tasks.jsonl` under `data/control/` as listed in `deployed_environment_smoke.json` → `optional_simulation_and_router_artifacts`.

---

## C) Micro-trade operator pack (live venue — operator-only)

**Precondition:** `micro_trade_readiness.json` has `"ok": true` **and** you have completed supervised live contracts (`LIVE_SINGLE_EXECUTION_VALIDATION_CONFIRM`, Coinbase keys, joint review, etc.) per your operator runbook — **not** automated here.

```bash
# 1) Readiness (fail-closed)
source /opt/ezra-runtime/env/common.env
export PYTHONPATH=/opt/ezra-private/trading-ai/src:/opt/ezra-public/trading-ai/src
export EZRAS_RUNTIME_ROOT=/opt/ezra-runtime
/opt/ezra-venv/bin/python /opt/ezra-public/trading-ai/scripts/server/micro_trade_readiness.py \
  --runtime-root /opt/ezra-runtime --public-root /opt/ezra-public --private-root /opt/ezra-private
jq .ok /opt/ezra-runtime/data/control/micro_trade_readiness.json

# 2) Controlled live readiness (deployment module — no orders)
/opt/ezra-venv/bin/python -m trading_ai.deployment controlled-live-readiness || true
test -f /opt/ezra-runtime/data/control/controlled_live_readiness.json && jq . /opt/ezra-runtime/data/control/controlled_live_readiness.json | head

# 3) Start Avenue A daemon ONCE (example — adjust mode in env; do not enable live until policy says so)
# export EZRAS_AVENUE_A_DAEMON_MODE=supervised_live   # example only
# /opt/ezra-venv/bin/python -m trading_ai.deployment avenue-a-daemon-once --quote-usd 10 --product-id BTC-USD

# 4) Monitor
sudo journalctl -u ezra-ops.service -f
tail -f /opt/ezra-runtime/logs/post_trade_log.md 2>/dev/null || true
tail -f /opt/ezra-runtime/databank/trade_events.jsonl 2>/dev/null || true

# 5) Stop safely
sudo systemctl stop ezra-ops.service ezra-research.service   # if you need a hard pause
```

---

## D) Local smoke chain (developer / CI)

```bash
bash /opt/ezra-public/trading-ai/scripts/server/server_side_smoke_chain.sh
```

Or from a dev clone:

```bash
bash scripts/server/server_side_smoke_chain.sh
```

---

## E) Operator-only (cannot be scripted in-repo)

- Coinbase API secrets and JWT private key material  
- `LIVE_SINGLE_EXECUTION_VALIDATION_CONFIRM=YES_I_UNDERSTAND_REAL_CAPITAL`  
- Joint review packet / `joint_review_latest.json` policy in production  
- DNS / TLS / firewall / `railway` vs bare-metal choice  
- Actual notional sizing and exchange approval  
