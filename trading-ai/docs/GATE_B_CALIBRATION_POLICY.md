# Gate B calibration / tuning policy

- **Baseline** — `GateBConfig` / env (`GATE_B_*`); conservative defaults, not performance guarantees.
- **Resolver** — `resolve_gate_b_tuning_artifact` in `shark/coinbase_spot/gate_b_tuning_resolver.py` emits a deterministic snapshot: account bucket (small / medium / large / unknown), optional slippage-based **tightening** of profit-exit buffer only, and `clamp_reasons`.
- **Calibration levels** (truthful labels, `gate_b_tuning_resolution_v2`) — `baseline_env_only` | `account_size_only_measured_slippage_unknown` | `slippage_only_deployable_unknown` | `assumed_slippage_buffer_only` | `partial_slippage_input` | `full_measured_slippage_and_deployable` (deployable **and** measured slippage both present — never from assumed slippage alone).
- **Safety** — Resolver does not widen stops, loosen exits, or increase aggression vs baseline; it may reduce concurrency / top-K on small accounts.

Operator status exposes `gate_b.tuning_resolution` and `gate_b_calibration_level`.
