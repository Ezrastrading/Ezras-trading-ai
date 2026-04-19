# Databank isolation and organism contract (locked behavior)

## Databank root resolution

Trade Intelligence files (`trade_events.jsonl`, score aggregates, summaries) live under a **single explicit root**:

1. **`TRADE_DATABANK_MEMORY_ROOT`** — if set, this path is the databank directory.
2. Else **`EZRAS_RUNTIME_ROOT/databank`** — session-scoped default for tests, runtime proof, and operators using a dedicated runtime tree.

If **neither** is set, `resolve_databank_root()` raises **`DatabankRootUnsetError`**. There is **no** silent fallback to `~/ezras-runtime` or `shark/memory/global` for databank data.

**Implication:** any process that calls federation (`load_federated_trades`) or writes databank rows must run with `EZRAS_RUNTIME_ROOT` and/or `TRADE_DATABANK_MEMORY_ROOT` set. Production entrypoints should set `EZRAS_RUNTIME_ROOT` before importing NTE/databank code paths.

**Storage architecture (local vs Supabase):** canonical maps and contracts live under `docs/runtime_storage/` — `storage_contract.md`, `storage_architecture_report.json`, `storage_path_map.json`, `supabase_sync_contract.json`. Local files under `EZRAS_RUNTIME_ROOT` remain the runtime source of truth; Supabase is optional remote mirror/inspection when `SUPABASE_URL` + `SUPABASE_KEY` are set (see contract for `SUPABASE_KEY` vs `SUPABASE_SERVICE_ROLE_KEY` alignment).

## Runtime proof harnesses

- **First-20 session** (`runtime_proof/first_twenty_session.py`): sets both `EZRAS_RUNTIME_ROOT` and `TRADE_DATABANK_MEMORY_ROOT` to the session’s runtime and `…/databank`.
- **Shadow proof** (`coinbase_shadow_paper_pass.py`): same pattern.
- **Stress harness** (`runtime_proof/organism_stress_harness.py`): writes `stress_proof/*.json` reports under the runtime root; no live capital. The harness sets `review_scheduler_state.json` → `suppress_all` so `tick_scheduler` does not fire live model reviews; explicit `run_full_review_cycle(..., skip_models=True)` cycles still exercise stubbed Claude/GPT.

## Multi-avenue fairness

`avenue_fairness_rollups` and `packet_truth.avenue_fairness` expose per-avenue counts, PnL, quality score, hard-stop and anomaly tallies, and USD vs play-money trade counts. Expected avenues and representation (`present` / `partial` / `missing`) are in `avenue_truth_contract` + `trade_truth` meta. Kalshi gaps must surface as **warnings**, not silent equality.

## Execution vs organism core

- **Organism core** (global layer): governance, federation merge, review packet, scheduler — consumes **normalized** federated trades and joint review snapshots.
- **Execution** (NTE/Shark/outlets): venue orders, adapters, databank append — emits events; failures should appear as truth/anomaly/state, not as hidden routing in the packet builder.

**Governance is the first decisive gate** on the NTE Coinbase new-entry path (`coinbase_engine._nte_entry_gates_coinbase`): `check_new_order_allowed_full` runs before strategy live-routing approval (`live_routing_permitted`). Strategy approval is **downstream execution policy**, not the top-level safety gate. See `governance_proof/governance_ordering_report.json`.

Artifact writers: `runtime_proof/execution_boundary_report.py` (`organism_core_inputs.json`, `execution_boundary_report.json`, `avenue_specific_behaviors.json`).

## What “agnostic” means here

The organism does **not** use strategy names, latency, or edge **to choose routes or governance outcomes**. Those fields may appear as **metadata** in packets and trade rows. It does **not** claim strategy alpha, latency optimality, or full venue parity where ingest is incomplete.

## Lock bundle artifacts (operator / CI)

Run:

`PYTHONPATH=src python3 scripts/organism_lock_bundle.py --root /path/to/writable/runtime`

Produces under that root (and subfolders):

- `governance_proof/governance_ordering_report.json`
- `isolation_proof/databank_isolation_report.json`
- `stress_proof/*.json` (scheduler, federation, artifact integrity, runtime proof)
- `soak_proof/soak_*.json`, `soak_report_summary.json` (long-run harness; bundle uses short test soak)
- `noise_proof/environment_noise_report.json`
- `parity_proof/avenue_parity_report.json`
- `kalshi_proof/kalshi_parity_status.json`, `kalshi_process_readiness.json`, `kalshi_isolation_report.json`
- `goal_proof/avenue_goal_progress.json`, `global_goal_progress.json`, `learning_log.json`
- `boundary_proof/organism_core_inputs.json`, `execution_boundary_report.json`, `avenue_specific_behaviors.json`
- `organism_lock_bundle_summary.json`

**Live first-20 (controlled):** use `runtime_proof/live_first_20_operator.py` preflight; session JSON as `live_first_20_session_report.json`; judge output via `write_live_judge_report()` as `live_first_20_judge_report.json`. No unconditional live enablement.

**Kalshi parallel namespace:** optional `KALSHI_RUNTIME_ROOT` for a Kalshi-only runtime tree (`kalshi_process_readiness.json`). Set `TRADE_DATABANK_MEMORY_ROOT=$KALSHI_RUNTIME_ROOT/databank` for a clean separate process. Federation reads still follow the active session’s env.

**Environment noise:** API/billing messages during bundle/stress runs are classified in `noise_proof/environment_noise_report.json` so they are not mistaken for organism integrity failures when reviews use stub models.
