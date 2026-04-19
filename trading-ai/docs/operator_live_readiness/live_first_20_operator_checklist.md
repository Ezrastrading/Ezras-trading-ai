# Supervised live first-20 — operator checklist (no auto-start)

Use only **after** `run_live_preflight` returns all critical checks PASS. This document does not place orders.

## 1. Export required env (see REQUIRED ENV SUMMARY in operator runbook)

Minimum for intentional live posture (preflight check 4):

- `COINBASE_ENABLED=true`
- `NTE_EXECUTION_MODE=live`
- `NTE_PAPER_MODE=false` (or unset)
- `LIVE_FIRST_20_ENABLED=true`
- `FIRST_TWENTY_ALLOW_LIVE=true`

Plus credentials, `EZRAS_RUNTIME_ROOT`, Supabase stance, and optional `LIVE_FIRST_20_QUOTE_NOTIONAL_USD`.

## 2. Smallest notional

- Engine default floor comes from `RollbackThresholds` / product rules (`_smallest_notional_usd()` in preflight — typically **≥ 10 USD** across defaults).
- If `LIVE_FIRST_20_QUOTE_NOTIONAL_USD` is set, it must be **≥** that minimum or preflight **fails** (check 5).

## 3. Rollback / abort (from `RollbackThresholds` in `first_twenty_session.py`)

| Trigger | Threshold |
|---------|-----------|
| Consecutive governance gate anomalies (enforcement on, unexpected deny) | `max_consecutive_gate_anomalies` = **5** |
| `process_closed_trade` local failures | `max_process_closed_local_failures` = **3** |
| Federation conflict spike in meta | `max_federation_conflict_spike` = **15** |
| Malformed lines in `review_scheduler_ticks.jsonl` | `max_scheduler_tick_parse_errors` = **0** (any bad line) |
| Joint review paused | `joint_review_paused_stops` = **true** — stop if live mode paused when readable |

Abort preflight-level failures: any critical preflight check FAIL — do not proceed.

## 4. Artifacts to watch during supervised run

| Artifact | What to watch |
|----------|----------------|
| `governance_gate_decisions.log` | Each entry gate decision; unexpected denies under enforcement |
| `shark/nte/memory/trade_memory.json` | Trades recorded vs expected count |
| `databank/trade_events.jsonl` | Append-only rows; no corrupt lines |
| `shark/memory/global/joint_review_latest.json` | Integrity / live_mode / stale signals |
| `shark/memory/global/review_scheduler_ticks.jsonl` | Zero malformed JSONL lines |
| Session archive under `live_first_20_sessions/<id>/` | Manifest + session report |

## 5. Stop conditions (operator)

- Any rollback threshold tripped.
- Exchange or process error loop (repeated order failures, auth errors).
- Governance enforcement blocks entries consistently.
- Operator judgment — manual stop always allowed.

## 6. Supervised start command

Preflight does **not** start the trading stack. The repo’s **live operator entry** is preflight-only in `scripts/live_coinbase_first_twenty.py`. The **actual process** that executes NTE live (your supervisor wrapper, systemd, or `run_shark` + NTE) must be started **explicitly** by the operator after preflight PASS — **not** automated here.

Typical pattern:

1. Run preflight (exit 0).
2. Start your **supervised** runtime (the same component you used in shadow verification), with the **same** `EZRAS_RUNTIME_ROOT` and env.
3. Monitor artifacts above until 20 trades complete or rollback fires.

**Exact binary/command** depends on your deployment (not embedded in preflight). Use the same entrypoint documented for your Coinbase NTE production run.

## 7. Final warning

Live capital is at risk once the live stack runs with live flags. There is **no** substitute for human supervision during the first-20 window.
