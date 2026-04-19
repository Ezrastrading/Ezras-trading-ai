# Exchange connectivity — what live preflight proves (Coinbase Advanced Trade)

This checklist matches `trading_ai.runtime_proof.live_first_20_operator.run_live_preflight` and `CoinbaseClient.get_accounts()` (`GET /api/v3/brokerage/accounts`).

## What is checked

| Step | Type | What it proves | Pass condition |
|------|------|----------------|----------------|
| 1 | Credential format | API key id string present | Non-empty `COINBASE_API_KEY_NAME` or `COINBASE_API_KEY` |
| 2 | PEM | Private key parses as EC | `cryptography.hazmat.primitives.serialization.load_pem_private_key` succeeds on PEM after `\n` unescape |
| 3 | Authenticated REST | JWT signing works and Coinbase accepts the call | `CoinbaseClient().get_accounts()` returns without exception; response has `accounts` list (possibly empty) |

## What is NOT checked by preflight

- **Trading permissions** for specific products (e.g. BTC-USD) — preflight does not place orders or verify product-level entitlements.
- **Portfolio scoping** — `COINBASE_PORTFOLIO_ID` may matter for balances; preflight only lists accounts.
- **Rate limits** under load — only a single `GET /accounts`.
- **WebSocket** user or market feeds — not exercised.
- **Sandbox vs production** — assumes production Advanced Trade URL (`https://api.coinbase.com/api/v3/brokerage`).

## Failure modes (check 3 detail string patterns)

| Symptom | Likely cause | Operator action |
|---------|--------------|-------------------|
| `missing_api_key_id:...` | No key id in env | Set `COINBASE_API_KEY_NAME` (CDP) or `COINBASE_API_KEY` |
| `missing_private_key:...` | No PEM | Set `COINBASE_API_PRIVATE_KEY` or `COINBASE_API_SECRET` |
| `pem_parse_failed:...` | Wrong format, RSA instead of EC, bad escapes | Use EC P-256 PEM with `BEGIN EC PRIVATE KEY`; in `.env` use `\n` for newlines |
| `rest_error:... CoinbaseAuthError 401` or `401 unauthorized` | Wrong key pair, revoked key, clock skew | Regenerate CDP key; verify system time (NTP) |
| `rest_error:... Coinbase HTTP 403` | Key lacks permission | CDP key must allow account read (and later trade) |
| `rest_error:... URLError` / network | DNS, firewall, proxy, offline | Fix egress; retry |
| `rest_error:... 429` | Rate limit (client may retry internally) | Back off |

## Pass/fail steps for operators

1. **Export** key id + PEM exactly as required (see live operator env reference).
2. **Run** preflight only: `PYTHONPATH=src python3 scripts/live_coinbase_first_twenty.py --preflight --runtime-root <EZRAS_RUNTIME_ROOT> --simulated-judge <judge.json>`.
3. **PASS** check `coinbase_credentials_validated` when detail contains `pem:... pem_load_ok` and `rest:... accounts_ok_count=N`.
4. **FAIL** — do not start supervised live until check 3 passes with real credentials.

## Same machine as production?

Preflight does not bind to a hostname. **Use the same OS environment** you will use for live (same env vars, same network egress, same clock) so JWT and TLS behavior match. Changing machines is allowed if env and network are equivalent.
