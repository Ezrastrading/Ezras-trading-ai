# Live micro operator pack (fail-closed)

Authoritative code: `trading_ai.deployment.live_micro_enablement` and CLI `python -m trading_ai.deployment live-micro-*`.

Server layout (defaults):

- `PUBLIC_DIR=/opt/ezra-public` — repo checkout (`/opt/ezra-public/trading-ai`)
- `PRIVATE_DIR=/opt/ezra-private` — secrets overlays (not described here)
- `RUNTIME_ROOT=/opt/ezra-runtime` — `EZRAS_RUNTIME_ROOT`, artifacts, state
- `VENV=/opt/ezra-venv` — interpreter

All live-micro JSON proof files are under **`${RUNTIME_ROOT}/data/control/`**:

| Artifact | Meaning |
|----------|---------|
| `live_enablement_request.json` | Operator intent + env contract snapshot |
| `live_session_limits.json` | Caps from env at write time |
| `live_preflight.json` | Kill switch, smoke/micro readiness freshness, governance, Coinbase env probe |
| `live_micro_readiness.json` | Preflight + import/write probes |
| `live_start_receipt.json` | Optional receipt when an approved live component starts |
| `live_guard_proof.json` | Snapshot of mode/env/kill switch (no orders) |
| `live_disable_receipt.json` | Audit trail when rolling back |
| `live_micro_session_state.json` | Session trades, cooldown, open exposure (micro bookkeeping) |
| `live_micro_force_halt.json` | **Pause file** — if present, **all live micro orders block** until `live-micro-resume` removes it |

## Required env (micro live)

Set in service env (e.g. `${RUNTIME_ROOT}/env/common.env` or ops overlay), then export in the shell that runs CLI:

- `EZRA_LIVE_MICRO_OPERATOR_CONFIRM=I_ACCEPT_MICRO_LIVE_CAPITAL_RISK_AND_LIMITS`
- `EZRA_LIVE_MICRO_MAX_NOTIONAL_USD` — per-trade cap (keep microscopic, e.g. `1`–`5`)
- `EZRA_LIVE_MICRO_MAX_DAILY_LOSS_USD`
- `EZRA_LIVE_MICRO_MAX_TOTAL_EXPOSURE_USD` — open exposure cap (session ledger)
- `EZRA_LIVE_MICRO_ALLOWED_PRODUCTS` — CSV, e.g. `BTC-USD`
- `EZRA_LIVE_MICRO_ALLOWED_AVENUE` — e.g. `COINBASE` or `A`
- `EZRA_LIVE_MICRO_ALLOWED_GATE` — e.g. `gate_a`
- `EZRA_LIVE_MICRO_MAX_TRADES_PER_SESSION`
- `EZRA_LIVE_MICRO_COOLDOWN_SEC`
- `EZRA_LIVE_MICRO_MAX_CONCURRENT_POSITIONS` — default `1` for one live position
- Optional: `EZRA_LIVE_MICRO_ALLOW_MULTI_PRODUCT=true` to allow multiple products in the CSV

Enable runtime only after proofs are fresh:

- `EZRA_LIVE_MICRO_ENABLED=true` (only after preflight + readiness + limits + enablement request succeed)

## Exact commands (aligned to `/opt/...`)

Replace nothing if your ops tree already exports `PUBLIC_DIR`, `PRIVATE_DIR`, `RUNTIME_ROOT`, `VENV`.

```bash
export PUBLIC_DIR="${PUBLIC_DIR:-/opt/ezra-public}"
export PRIVATE_DIR="${PRIVATE_DIR:-/opt/ezra-private}"
export RUNTIME_ROOT="${RUNTIME_ROOT:-/opt/ezra-runtime}"
export VENV="${VENV:-/opt/ezra-venv}"
export EZRAS_RUNTIME_ROOT="${RUNTIME_ROOT}"
PY="${VENV}/bin/python"
REPO="${PUBLIC_DIR}/trading-ai"
cd "${REPO}"
```

### Install / update services

```bash
sudo "${REPO}/scripts/server/install_or_update_services.sh"
# then:
sudo systemctl restart ezra-ops.service ezra-research.service
```

### Enable live micro mode (artifacts + caps; does not send orders)

```bash
cd "${REPO}"
# 1) Operator-bound env must already be exported in this shell (see Required env).
"${PY}" -m trading_ai.deployment live-micro-enablement-request \
  --operator "$(whoami)" --note "micro live enablement"
"${PY}" -m trading_ai.deployment live-micro-write-session-limits
```

### Live preflight

```bash
"${PY}" -m trading_ai.deployment live-micro-preflight
```

### Live micro readiness

```bash
"${PY}" -m trading_ai.deployment live-micro-readiness
```

### Guard proof snapshot (optional)

```bash
"${PY}" -m trading_ai.deployment live-micro-guard-proof
```

### Record start (after systemd/unit or supervised start)

```bash
"${PY}" -m trading_ai.deployment live-micro-record-start \
  --component "ezra-ops_supervised_live" --detail-json '{"pid":"'"$$"'"}'
```

### Approved live daemon path (single cycle; uses same validation as operator)

```bash
# Avenue A supervised/autonomous modes per existing env — micro contract applies when EZRA_LIVE_MICRO_ENABLED=true
"${PY}" -m trading_ai.deployment avenue-a-daemon-once --quote-usd 1.0 --product-id BTC-USD
```

### View logs (example)

```bash
sudo journalctl -u ezra-ops.service -f --no-pager
```

### Pause live (disable wins — blocks venue path without unsetting env)

```bash
"${PY}" -m trading_ai.deployment live-micro-pause --operator "$(whoami)" --reason "operator_pause"
```

### Hard stop live (pause + unset env in service file — operator action)

1. Run pause (above).
2. Remove `EZRA_LIVE_MICRO_ENABLED` or set `false` in the **service** environment.
3. Restart units.

```bash
"${PY}" -m trading_ai.deployment live-micro-disable-receipt --reason "hard_stop" --operator "$(whoami)"
sudo systemctl restart ezra-ops.service
```

### Revert to non-live

```bash
"${PY}" -m trading_ai.deployment live-micro-disable-receipt --reason "revert_nonlive" --operator "$(whoami)"
# Unset EZRA_LIVE_MICRO_ENABLED + ensure NTE_EXECUTION_MODE / venue flags match paper/non-live policy; restart services.
```

### Resume after operator pause (removes halt file)

```bash
"${PY}" -m trading_ai.deployment live-micro-resume
```

### Verify proof artifacts / contract after start

```bash
"${PY}" -m trading_ai.deployment live-micro-verify-contract
ls -la "${RUNTIME_ROOT}/data/control/live_"*.json
```

## Smoke / tests (repo root)

```bash
cd "${REPO}"
"${PY}" -m pytest tests/test_live_micro_enablement.py -q
"${REPO}/scripts/server/live_micro_smoke.sh"
```
