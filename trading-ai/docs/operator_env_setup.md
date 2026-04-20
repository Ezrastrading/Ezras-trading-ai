# Operator environment setup (no secrets in this file)

## 0. Python / OpenSSL / venv (read this first)

- Supported Python: **3.11+** (repo standard **3.11.8**, see `.python-version` and `pyproject.toml`).
- **OpenSSL-backed** interpreter required for HTTPS (not macOS **LibreSSL**). See `docs/SSL_RUNTIME.md`.
- From a clean checkout on macOS with Homebrew + pyenv:

  ```bash
  cd trading-ai
  bash scripts/bootstrap_runtime.sh
  source venv/bin/activate
  ```

- Smaller step (if Python 3.11.8 is already correct):

  ```bash
  bash scripts/create_venv.sh
  source venv/bin/activate
  ```

- Verify SSL and env:

  ```bash
  PYTHONPATH=src python -m trading_ai.deployment check-env
  PYTHONPATH=src python -m trading_ai validate-env
  ```

## 1. Runtime root

```bash
export EZRAS_RUNTIME_ROOT="/path/to/persistent/writable/root"
mkdir -p "$EZRAS_RUNTIME_ROOT"
export TRADE_DATABANK_MEMORY_ROOT="$EZRAS_RUNTIME_ROOT/databank"
mkdir -p "$TRADE_DATABANK_MEMORY_ROOT"
```

## 2. Coinbase Advanced Trade

Use **either** name pair (see `docs/runtime_storage/coinbase_credential_contract.json`):

- `COINBASE_API_KEY_NAME` + `COINBASE_API_PRIVATE_KEY`, or  
- `COINBASE_API_KEY` + `COINBASE_API_SECRET`

**Shell-safe pattern:** keep PEM in a file and export without pasting into `.env`:

```bash
export COINBASE_API_KEY_NAME="organizations/<org>/apiKeys/<id>"
export COINBASE_API_PRIVATE_KEY="$(cat /secure/path/ec_private.pem)"
```

**Python/dotenv pattern:** single line with `\n` escapes for PEM inside `.env` — load with `load_dotenv`, **not** `source` in bash.

**Verify without heredocs or inline Python** (zsh/bash/CI-safe; prints `MISSING` / `SET (len=…)` only):

```bash
PYTHONPATH=src python3 -m trading_ai.deployment check-env
```

The same command includes a short **Python / SSL** block (`ssl.OPENSSL_VERSION`, `ssl_guard_would_pass`). Use a Python built against **OpenSSL** (not macOS **LibreSSL**); see `docs/SSL_RUNTIME.md`.

## 3. Remote sync (preferred when Supabase exists)

```bash
export SUPABASE_URL="https://<project>.supabase.co"
export SUPABASE_KEY="<anon_or_service_role_jwt>"
```

`nte/databank/supabase_trade_sync.py` resolves JWT via **`trading_ai.global_layer.supabase_env_keys.resolve_supabase_jwt_key`**: **`SUPABASE_KEY`** first, else **`SUPABASE_SERVICE_ROLE_KEY`**. You may set either (or both with the same value for clarity).

## 4. Governance — controlled first-20 profile

See `docs/runtime_storage/governance_live_profile.json`. Example:

```bash
export GOVERNANCE_ORDER_ENFORCEMENT=true
export GOVERNANCE_CAUTION_BLOCK_ENTRIES=true
export GOVERNANCE_MISSING_JOINT_BLOCKS=true
export GOVERNANCE_STALE_JOINT_BLOCKS=true
export GOVERNANCE_UNKNOWN_MODE_BLOCKS=true
export GOVERNANCE_DEGRADED_INTEGRITY_BLOCKS=true
export GOVERNANCE_JOINT_STALE_HOURS=48
```

## 5. Live preflight (Coinbase first-20)

After env is set, run `scripts/live_coinbase_first_twenty.py --preflight` with `--runtime-root` and `--simulated-judge`. Do not use `/tmp` for production roots.

## 6. Gate B selection snapshot — reading `selected_symbols = []`

Artifact: `data/control/gate_b_selection_snapshot.json` (from deployment / selection runner).

- **`measured_spread_bps` is `null`** when the quote was missing, stale, or could not be parsed — this is **not** a claim that the market had a 9999 bps spread.
- Use **`selection_summary.counts_by_rejection_category`** and **`selection_summary.no_selection_reason`** to see whether failures were feed errors, stale quotes, spread policy, or structural no-candidate state.
- Version **`gate_b_selection_snapshot_v3`** adds capital split truth, tuning snapshot, failure taxonomy (`failure_codes`, `rejection_kind`), supervised-operator blockers, and market-data quality summaries. See **`docs/GATE_B_OPERATOR_TRUTH_MODEL.md`**.

## 7. What not to do

- Do not `source .env` in bash if the file contains raw multiline PEM.
- Do not commit real `.env` files.
