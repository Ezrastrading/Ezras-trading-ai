# Databank write halt policy

When consecutive logical `trade_events` upserts fail (after retries), the databank layer increments a streak and may activate kill-switch reason `SUPABASE_DATABANK_WRITE_FAILURE_THRESHOLD`.

- **Threshold env:** `SUPABASE_DATABANK_WRITE_FAILURE_THRESHOLD` (default `3` if unset or invalid).
- **Truth files:** `data/control/databank_write_halt_truth.json`, `data/control/databank_write_halt_state.json`.
- **System guard:** Still records Supabase streaks via `record_supabase_ok` / `record_supabase_failure` for compatibility.
- **Recovery:** Successful remote write resets the databank streak; kill-switch / recovery_engine clearing remains operator/policy gated.
