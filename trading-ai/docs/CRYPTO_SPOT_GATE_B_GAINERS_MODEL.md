# Gate B — crypto spot momentum / gainers lane

**Operator truth model (status fields, empty selection, calibration):** see [`GATE_B_OPERATOR_TRUTH_MODEL.md`](./GATE_B_OPERATOR_TRUTH_MODEL.md).

## Role

- **Gate A** — core spot / NTE execution (existing).
- **Gate B** — shorter hold, momentum-qualified entries, strict exits (profit target, trailing stop from peak, hard stop).

Gate B is **not** “buy top % movers”; ranking combines momentum, liquidity, and exhaustion risk.

## Capital split (defaults)

- 50% deployable budget **Gate A** / 50% **Gate B** (`compute_gate_allocation_split`).
- Within Gate A: 50% **majors sleeve (BTC/ETH)** / 50% **other supported products** (configurable shares).

## Exit discipline (defaults)

- Profit target ~**10%** from entry (`GATE_B_PROFIT_TARGET_PCT`).
- Trailing ~**3%** from **peak since entry** (`GATE_B_TRAILING_STOP_PEAK_PCT`).
- Hard stop from entry ~**3–5%** (`GATE_B_HARD_STOP_ENTRY_PCT`, `GATE_B_MAX_PER_POSITION_LOSS_PCT`).
- Max hold and daily Gate B drawdown caps are **percentage-based** — see `GateBConfig` / env overrides.

## Asset behavior

- Majors vs alts differ in **tick size, spread, volatility, and noise**; the same % move has different **dollar** and **risk** implications.
- Low-priced tokens may show large **%** moves with poor **liquidity** — scanner rejects low `liquidity_score` and high `exhaustion_risk` candidates.

## Failure modes

- Chasing exhausted blow-offs, ignoring spread, confusing **quote balance** with **total equity**, or disabling reconciliation to “pass” checks.

## Implementation modules (code)

- `shark/coinbase_spot/gate_b_engine.py` — orchestrates liquidity gate, breakout filter, ranking, regime, correlation, re-entry, data quality, exits (faster cadence + sudden-drop events).
- `shark/coinbase_spot/execution_reality.py` — intended vs actual prices, slippage bps, execution quality, theoretical vs actual PnL.
- `shark/coinbase_spot/liquidity_gate.py`, `breakout_filter.py`, `gate_b_regime.py`, `gate_b_correlation.py`, `gate_b_reentry.py`, `gate_b_edge_stats.py`, `gate_b_data_quality.py`, `gate_b_events.py`.
- `nte/unified_portfolio_coinbase.py` — total USD equity across USD/USDC + crypto marks.
- `review/ceo_gate_reports.py` — Gate A / B / global CEO JSON shells.
