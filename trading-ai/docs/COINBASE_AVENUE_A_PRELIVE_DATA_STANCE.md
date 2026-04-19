# Coinbase Avenue A — Pre-Live Data & Supabase Stance (V1)

**Decision: Option B — local-first launch truth for controlled first-20-trades**

## What counts as canonical truth for V1

- **Primary:** `EZRAS_RUNTIME_ROOT`-scoped **JSON / JSONL** under:
  - NTE: `shark/nte/memory/trade_memory.json`
  - Databank: `TRADE_DATABANK_MEMORY_ROOT` (default: `shark/memory/global`) — `trade_events.jsonl`, scores, summaries
  - Organism: `shark/memory/global/review_packet_latest.json`, `joint_review_latest.json`, `review_scheduler_ticks.jsonl`
- **Federation:** `trade_truth.load_federated_trades` merges memory + databank with explicit `truth_provenance` (no silent overwrite).

## Supabase / remote sync

- **Not required** to declare the Avenue A pipeline “truth-complete” for a **controlled first-20-trades** launch when:
  - Local append paths succeed (`process_closed_trade` stages `local_raw_event`, summaries, learning hooks).
  - Operators accept that **`process_closed_trade` may report `ok: false`** when `SUPABASE_URL` / `SUPABASE_KEY` are absent, **only because** `supabase_trade_events` failed — **not** because local truth failed.

## When Supabase becomes required (policy upgrade)

- Cross-host replication, fleet dashboards, or compliance requiring **remote** durability before scaling beyond first-20.
- Require successful `supabase_trade_events` upsert in staging **before** removing the “local-first” caveat.

## Non-blocking classification

- **`supabase_upsert_failed`** in a dev/staging environment with valid local JSONL: treat as **non-blocking for first-20** if local artifacts pass integrity checks.
- **Blocking:** corrupted local files, duplicate `trade_id` handling errors in local store, or missing federated rows when memory + databank both claim the close.

This document is the **explicit** resolution for pre-live blocker **#4 (Supabase operational stance)**.
