# Coinbase Avenue 1 — first 24–72h monitoring plan

Goal: **truth-gathering and survival**, not maximum trade count. Check signals, not hope.

## Cadence

| When | What |
|------|------|
| **Continuous** | Process up; no unhandled exceptions; disk not full |
| **Every 15–30 min** | Glance: open positions, pending limits, `system_health.json`, last router log lines |
| **2× daily** | CEO-style review (midday ~12:00 ET, end ~17:00 ET): A vs B, post-fee expectancy, stale pending rate, maker vs taker reality |
| **Daily close** | Export or snapshot `capital_ledger.json`, `trade_memory.json`, `shadow_compare_events.json` |

## What to watch (red flags)

1. **Live-order guard** — any unexpected `Live order blocked` in prod without an obvious env mistake.
2. **User stream** — if logs show persistent polling / `user stream stale`, fix WS before adding size.
3. **Net edge** — rising count of `net_edge_fail` / `rejected` in router logs while still taking trades → logic drift.
4. **Degraded mode** — entries should stop; exits must still complete. If entries continue → stop and fix.
5. **Stale pendings** — if cancels spike, revisit `stale_sec` for A vs B (research priority #1).
6. **Fees** — compare realized PnL vs notional; if post-fee expectancy stays negative after 48h, do **not** widen size.

## Commands (from repo root, `PYTHONPATH=src`)

- Pre-live checklist: `python3 scripts/pre_live_verification.py`
- NTE smoke: `pytest tests/test_nte_hardening_smoke.py tests/test_live_order_guard_bypass.py -q`
- Grep today’s router lines: `rg "NTE router eval" ~/.ezras-runtime/...` (or your `EZRAS_RUNTIME_ROOT`) / log aggregator

## Stop-trading triggers (manual)

- Daily loss approaches **4%** hard cap or repeated **degraded** with entries still firing.
- **Three losses same strategy** in a row (launch flag) — pause and review before re-enabling.
- Any bypass suspicion on order paths — run `pre_live_verification.py` again.

## After 72 hours

- Reconcile **ledger** vs exchange history; tune **maker/taker fee** assumptions in `FeeAssumptions`.
- Adjust **A vs B weights** only with evidence from `shadow_compare_events` and realized maker fill rate.
