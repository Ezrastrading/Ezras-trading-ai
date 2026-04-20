# Operator environment setup (no secrets in this file)

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

## 6. What not to do

- Do not `source .env` in bash if the file contains raw multiline PEM.
- Do not commit real `.env` files.
