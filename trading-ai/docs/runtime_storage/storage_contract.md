# Storage contract — local truth, remote mirror

## Local source of truth

- **Execution + organism:** Files under **`EZRAS_RUNTIME_ROOT`** drive runtime behavior: NTE memory (`shark/nte/memory`), global review/joint artifacts (`shark/memory/global`), shark state (`shark/state`), and **`governance_gate_decisions.log`** at the runtime root.
- **Trade Intelligence:** Append-only **`trade_events.jsonl`** under the resolved **databank root** (`TRADE_DATABANK_MEMORY_ROOT` or `EZRAS_RUNTIME_ROOT/databank`). Resolution is explicit — no silent global fallback.

## Remote source of truth

- **Supabase** is **not** the primary writer for the live loop. It is a **replica / query surface** when `SUPABASE_URL` and `SUPABASE_KEY` are set and sync functions succeed.
- **Shark optional push:** `shark/remote_state.py` can push selected JSON state keys to table `shark_state`.
- **Databank sync:** `nte/databank/supabase_trade_sync.py` upserts rows to **`trade_events`** (and helpers for other tables). Failures log warnings; **local rows remain**.

## What is mirrored

| Local artifact | Remote (when configured) |
|----------------|---------------------------|
| Databank trade rows | `trade_events` table (upsert by `trade_id`) |
| Selected shark JSON files | `shark_state` (keys: capital, positions, etc.) |
| Aggregates / summaries | Optional via `sync_summary_batch` to named tables |

## What stays local-only

- **`joint_review_latest.json`**, **`review_packet_latest.json`**, **`review_scheduler_ticks.jsonl`** — organism core; not bulk-synced by `supabase_trade_sync` in the default path.
- **`governance_gate_decisions.log`** — local audit file.
- **Session archives** (`first_20_sessions/`, `live_first_20_sessions/`) — local manifests/reports unless you add a separate pipeline.

## Required for live

1. Writable **`EZRAS_RUNTIME_ROOT`**
2. Resolvable **databank root** (env as above)
3. Writable **governance log** path (preflight check)
4. For **remote-sync stance:** `SUPABASE_URL` + key material accepted by code (`SUPABASE_KEY` for databank client; preflight also accepts `SUPABASE_SERVICE_ROLE_KEY` — align with how you create the Supabase client in your deployment)

## Mirrored vs required

- **Mirrored:** Optional for correctness of *local* execution; required only for your operational requirement of remote durability/inspection.
- **Required for live capital:** Local roots + Coinbase credentials + governance/policy — not Supabase *per se* unless your operator policy mandates it.
