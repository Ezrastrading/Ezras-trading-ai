# Gate B — operator truth model (read this first)

Gate B answers three honest questions: **what was measured**, **what was inferred or defaulted**, and **what was rejected (and why)**. It does **not** grant autonomous live permission; supervised readiness is operational clarity only.

## Snapshot versions

- **`gate_b_selection_snapshot_v3`** — selection JSON under `data/control/gate_b_selection_snapshot.json` with capital split truth, tuning snapshot, failure taxonomy, and supervised-operator blockers.
- **`gate_b_truth_model_v1`** — shared field semantics for gainers selection + momentum engine pre-rank rejections.

## Spread (`measured_spread_bps`)

| `spread_measurement_status` | Meaning |
|------------------------------|---------|
| `measured` | Bid/ask and mid were usable; `measured_spread_bps` is \((ask-bid)/mid \times 10{,}000\). |
| `unavailable` | No numeric spread is reported. Use `spread_unavailable_reason` (e.g. `bid_or_ask_missing_for_spread`, `quote_stale_gt_120s`, `ticker_http_error:…`). |

There is **no** sentinel like `9999` bps presented as a measurement. Internal ranking may use large negative scores for failed rows; that is **not** a spread.

## Momentum proxy (gainers snapshot)

- **`momentum_proxy`** — only set when the candidate **passed** filters and a mid existed; otherwise `null`.
- **`momentum_proxy_status`** — `derived_bid_ask_width_over_mid` when computed; `not_computable_quote_failed_or_policy` when not; `not_applicable_feed_error` on HTTP/JSON feed failure.

## Capital

- **`deployable_quote_usd`** passed into selection → `compute_coinbase_gate_capital_split` → **`gate_b_usd`** when `ok` is true.
- If split **`ok` is false**, `selected_symbols` is **empty**, `capital_budget_allocated_usd` is **null**, and `gate_b_selection_state` is **`empty_capital_gate`**. This is fail-closed for budget truth.

## Calibration (`resolve_gate_b_tuning_artifact`)

| `calibration_level` | When |
|---------------------|------|
| `full_measured_slippage_and_deployable` | Deployable USD > 0 **and** `measured_slippage_bps` present. |
| `account_size_only_measured_slippage_unknown` | Deployable known; slippage not measured. |
| `slippage_only_deployable_unknown` | Measured slippage only. |
| `baseline_env_only` | Neither reliable input. |

`calibration_truth_detail` and `tuning_inputs_visible` spell out exactly what was used. **“Full” calibration never uses assumed slippage alone.**

## Failure codes (compact)

Examples: `missing_market_data`, `stale_market_data`, `spread_too_wide_measured`, `liquidity_too_thin`, `momentum_too_weak`, `excluded_by_policy`, `excluded_by_cooldown`, `capital_not_available`, `duplicate_or_locked_symbol`, `structural_candidate_error`.

Each candidate row includes `failure_codes`, `rejection_kind` (`data_quality` | `market_policy` | `none`), and `candidate_seen` / `candidate_evaluable` / `candidate_passed`.

## Empty `selected_symbols`

Check `selection_summary.no_selection_reason` and `gate_b_selection_state`. Common cases: all feed errors, all quotes missing/stale, all rejected by spread policy, capital split invalid, or no passing candidates under policy.

## Supervised operator readiness

- **`gate_b_supervised_operator_ready`** — true when the artifact itself is coherent for operators (e.g. not every ticker failed at HTTP). **Not** autonomous permission.
- **`gate_b_supervised_operator_blockers`** — explicit strings when false (e.g. capital split invalid, all feed errors).

## Engine pre-rank rejections

`GateBMomentumEngine.evaluate_entry_candidates` returns **`pre_rank_rejections`**: structured rows for data-quality, liquidity, breakout, correlation, and re-entry stages, using the same taxonomy helpers as `gate_b_truth.py`.
