# Coinbase Avenue 1 — final launch card (PASS / FAIL)

Work **A → Q** in order. Any **FAIL** → do not go live until fixed.

## A — Environment & mode

- `NTE_EXECUTION_MODE` = live only when intended  
- `NTE_PAPER_MODE` = false  
- `NTE_DRY_RUN` = false  
- `COINBASE_ENABLED` = true  
- `NTE_LIVE_TRADING_ENABLED` = true  
- API keys readable; JWT signing works  

## B — Live order guard

- Paper / dry-run / missing creds / pauses block orders  
- Raw `_request("/orders")` cannot bypass guard  
- Limit, market, cancel paths go through guard  

## C — WebSocket — market data

- Connect OK; subscribe within ~5s (see logs: `Advanced Trade WS subscribed ticker+heartbeats`)  
- Heartbeats subscribed and received  
- Stale detection uses last activity including heartbeats (`last_feed_activity_age_sec`)  

## D — WebSocket — user stream (critical)

- Connect OK; JWT accepted (no auth rejection)  
- Fresh JWT per subscribe message (`heartbeats` then `user`, each with its own token)  
- Logs: `open_lag_ms` / `subscribe_send_ms`  
- Heartbeats + order events; `NTE_COINBASE_USER_WS_JWT_MODE=legacy_uri` only if CDP minimal fails  

**Probe:** `python scripts/coinbase_ws_live_probe.py` (with credentials for user leg)

## E — Degraded mode

- Bad market or user feed → entries off, exits allowed, `system_health.json` updated  

## F — Product rules

- `validate_order_size` rejects bad size / notional / increment for BTC-USD / ETH-USD  
- Tests: `pytest tests/test_product_rules.py`  

## G — Net edge gate

- Weak edge rejected; strong edge allowed (`tests/test_ab_router_launch.py` + logs)  
- Fee defaults conservative (`NTE_FEE_MAKER_PCT` / `NTE_FEE_TAKER_PCT` or tier from API)  

## H — Can-open-risk gate

- Daily loss / pending / degraded / strategy / health gates enforced  

## I — Capital ledger

- Deposits change equity, not weekly profit; closed trades update PnL with fees  

## J — A/B router

- Scores, chosen route, rejected reasons in logs and `shadow_compare_events.json`  
- Post-fee fields: `est_round_trip_cost_bps`, fee %, `expected_move_bps` on shadow rows  
- C remains sandbox-only  

## K — Execution flow

- Limit entry, post-only, pending/stale cancel, market exit  

## L — Shadow compare

- Live decision + paper mirror rows present when shadow enabled  

## M — CEO loop

- Twice-daily reviews and outputs as configured  

## N–P — Engines & smoke

- Knowledge / progression engines behave; full test suite green  

## Q — First live session clamp

Set **`NTE_LAUNCH_CLAMP=1`** for session one:

- `clamp_max_open_positions` (default 1)  
- Equity cap per trade via `clamp_equity_per_trade_pct_max` (default 10%)  
- After `clamp_pause_entries_after_consecutive_losses` (default 2) consecutive losses → **no new entries** until streak clears or clamp disabled  

---

## Go / no-go

**Go live** only if A–Q are PASS, user stream is stable, fees are real or conservative, and shadow + route truth logging is complete.
