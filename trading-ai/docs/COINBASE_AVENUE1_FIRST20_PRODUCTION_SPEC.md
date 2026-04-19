# Coinbase Avenue 1 — First 20 Production Spec

**Purpose**

This file defines the exact production requirements for Coinbase Avenue 1 launch, including the first-20-trades diagnostic phase, Route A / Route B routing, fee truth, execution timing truth, databank persistence, shadow exploration, live monitoring, CEO review integration, and hard-stop policy.

This file is the source of truth for the first live Coinbase launch.

---

## 1. Launch objective

The first 20 live Coinbase trades are not a profit-maximization batch.

They are a diagnostic batch designed to answer:

- whether execution is clean
- whether Route A or Route B has real post-fee edge
- whether the system’s data writes are complete and trustworthy
- whether shadow exploration produces useful candidates
- whether CEO sessions and learning hooks are updating correctly
- whether live monitoring and hard-stop logic are functioning under real market conditions

Avenue 1 is not considered “validated” until the first-20 report is complete and reviewed.

---

## 2. Coinbase platform truths the engine must respect

The system must be aligned with Coinbase Advanced Trade production behavior:

- Coinbase documents separate WebSocket endpoints for Market Data and User Order Data.
- Coinbase documents WebSocket JWTs for these channels and notes that WebSocket auth tokens expire after a short period, so subscription auth must be fresh.
- Coinbase documents current fee-tier retrieval through Get Transaction Summary, including `fee_tier.maker_fee_rate`, `fee_tier.taker_fee_rate`, `pricing_tier`, and `has_cost_plus_commission`.
- Coinbase documents order time-in-force and order behavior including GTC, GTD, IOC, market orders, limit orders, and fill policies, which must drive route execution selection.

---

## 3. Launch modes

The engine must support the following explicit modes:

### 3.1 `launch_diagnostic`

Used for the first 20 live trades.

Rules:

- strict position clamp
- strict route mix policy
- approved live routes only
- all exploration routes shadow-only
- all enhanced logging enabled
- all CEO/learning hooks enabled
- no dynamic live self-promotion of new strategies

### 3.2 `live_locked`

Used after first-20 validation.

Rules:

- only approved strategies trade live
- promoted candidates must have passed validation gates
- live self-modification forbidden
- parameter changes require config version bump

### 3.3 `research_shadow`

Used continuously in background.

Rules:

- no real orders
- may evaluate alternative routes, offsets, filters, hold windows, exit styles, product subsets
- must log opportunity and candidate performance
- may propose promotions

### 3.4 `paused`

No new entries. Existing exits and emergency management continue.

---

## 4. Products allowed in first-20

The first 20 live trades must be limited to:

- BTC-USD
- ETH-USD

Optional third product allowed only if:

- spread profile is clean
- liquidity is proven
- product-specific validation has passed in shadow mode

The engine must not open first-20 live trades in a product outside this allowlist.

---

## 5. Route definitions

### 5.1 Route A — `mean_reversion_maker`

This is the default live path for first-20.

**Intent:**

- capture modest post-fee edge through cleaner entry quality
- reduce taker-cost distortion
- learn signal quality with minimal execution noise

**Entry requirements:**

- product in allowed set
- market WebSocket fresh
- system health healthy
- no hard-stop active
- spread below configured maximum
- no breakout/instability filter triggered
- expected edge after session fee truth is positive and above buffer
- limit/post-only order would not immediately cross

**Order style:**

- maker-first limit
- post-only where supported by current order interface
- cancel if stale
- no chase repricing loop beyond configured maximum attempts

**Exit requirements:**

- small predefined profit target OR
- strict stop OR
- strict max-hold timer OR
- system-health degradation OR
- stale-feed emergency exit policy

**Expected role in first 20:** 12 to 14 trades

### 5.2 Route B — `continuation_pullback_taker`

This is the selective fast path for first-20.

**Intent:**

- capture stronger short-burst continuation setups when waiting for maker fill likely loses the move

**Entry requirements:**

- product in allowed set
- market WebSocket fresh
- system health healthy
- no hard-stop active
- momentum/continuation signal strength above threshold
- expected move clearly exceeds spread + session fees + slippage buffer
- adverse-selection risk below threshold

**Order style:**

- market or aggressive IOC only when expected post-fee edge remains positive
- no route-B entry allowed on borderline setups

**Exit requirements:**

- fast profit capture OR
- strict stop OR
- strict max-hold timer OR
- health degradation emergency exit

**Expected role in first 20:** 6 to 8 trades

### 5.3 Forbidden live routes during first-20

All other strategies:

- shadow-only
- no real order placement
- no auto-promotion during first-20 batch

---

## 6. First-20 mix policy

The engine must hard-enforce a diagnostic route mix during `launch_diagnostic`.

Rules:

- target Route A count: 12–14
- target Route B count: 6–8
- if Route B quota is reached early, additional valid B setups become shadow-only
- if Route A quota is reached early, further A setups may be held or converted to shadow logging depending on session policy
- no non-approved route may consume live quota

The report must explicitly show:

- A live count
- B live count
- shadow candidate count
- rejected candidate count

---

## 7. Session fee truth

Before first live trade in every session, the engine must call Coinbase transaction summary and persist a fee snapshot.

**Required fields:**

- `fee_snapshot_id`
- `fee_snapshot_ts`
- `maker_fee_pct`
- `taker_fee_pct`
- `pricing_tier`
- `advanced_trade_only_volume`
- `advanced_trade_only_fees`
- `has_cost_plus_commission`

**Requirements:**

- live routing math must use fee snapshot values when available
- environment fallback fee assumptions are allowed only if the endpoint fails
- if fallback fees are used, this must be logged as a material session warning

No live first-20 session is considered clean if no fee snapshot exists. Coinbase documents these fields in the transaction summary response.

---

## 8. Execution timing truth

Each trade and candidate must support high-fidelity timing.

**Required timestamps:**

- `signal_ts`
- `route_locked_at`
- `order_submit_ts`
- `order_ack_ts`
- `first_fill_ts`
- `full_fill_ts`
- `cancel_ts`
- `exit_signal_ts`
- `exit_submit_ts`
- `exit_ack_ts`
- `exit_fill_ts`

**Required derived metrics:**

- `signal_to_submit_ms`
- `submit_to_ack_ms`
- `ack_to_first_fill_ms`
- `submit_to_full_fill_ms`
- `submit_to_cancel_ms`
- `exit_signal_to_submit_ms`
- `exit_submit_to_fill_ms`

These fields must be persisted into:

- local closed-trade memory
- global databank trade event row
- first-20 report source object

A first-20 report is incomplete if these fields are missing.

---

## 9. Route lock metadata

Every live and shadow trade record must include immutable route metadata.

**Required fields:**

- `route_name`
- `route_family`
- `route_version`
- `route_locked_at`
- `route_reason_short`
- `deployment_mode`
- `fee_snapshot_id`
- `router_score_a`
- `router_score_b`
- `router_reason`
- `expected_edge_bps`
- `expected_move_bps`
- `est_round_trip_cost_bps`
- `entry_maker_intent`
- `entry_execution`

**Purpose:**

- allow exact replay of why the engine chose the trade
- prevent post-hoc confusion after router upgrades
- support CEO review and later model comparison

---

## 10. Databank persistence requirements

A closed live trade is not considered valid unless all required persistence stages complete.

**Required write stages:**

1. raw closed-trade row appended locally
2. closed-trade row appended to avenue-specific log
3. trade score row updated
4. Supabase trade event row attempted
5. summaries refreshed
6. goal snapshot hook updated
7. CEO snapshot hook updated
8. learning hook appended
9. write verification row appended
10. databank health refreshed

**Failure handling:**

- if local write fails: session hard-stop, no new entries
- if Supabase fails but local write succeeds: mark partial failure, continue only if policy allows
- if CEO/learning hook fails: warn loudly, continue only if configured as non-fatal
- if verification file fails: session hard-stop

---

## 11. Live monitoring requirements

The live dashboard must expose:

**system state**

- engine mode
- hard-stop state
- system health
- market WS freshness
- user WS freshness
- fee snapshot health
- current session launch phase

**execution state**

- open positions
- pending orders
- recent fills
- stale cancels
- market entries
- limit entries placed
- limit entries filled

**quality state**

- recent trade quality scores
- route A vs B distribution
- expected edge vs realized move
- slippage summaries
- consecutive losses
- rolling net PnL
- pause state

**learning state**

- last CEO review ts
- last learning hook ts
- exploration candidates awaiting review
- promoted candidates count
- rejected candidates count

---

## 12. Hard-stop rules

Any of the following must stop new entries immediately:

- market WebSocket stale beyond threshold
- user WebSocket stale beyond threshold and no polling fallback
- local write verification failed
- three consecutive live losses in diagnostic mode
- rolling last-10 net breach threshold
- anomalous slippage cluster
- dashboard health marked unhealthy
- fee snapshot missing in diagnostic mode after bootstrap retry window

**Hard-stop behavior:**

- no new entries
- exits/emergency management remain active
- event written to governance log
- CEO review queue flagged

---

## 13. Shadow exploration requirements

The engine must always run a background shadow layer during market hours, but shadow exploration must never place real orders in `launch_diagnostic`.

Each candidate row must include:

- `candidate_id`
- `candidate_ts`
- `product_id`
- `strategy_candidate`
- `strategy_family`
- `route_candidate`
- `entry_type_candidate`
- `expected_edge_bps`
- `expected_move_bps`
- `spread_bps`
- `fee_snapshot_id`
- `reject_reason` or `shadow_reason`
- `would_enter`
- `forward_move_30s_bps`
- `forward_move_60s_bps`
- `forward_move_180s_bps`
- `shadow_gross_bps`
- `shadow_net_bps_after_fee_est`
- `shadow_max_adverse_bps`
- `shadow_max_favorable_bps`

**Purpose:**

- identify better offsets
- identify better hold windows
- identify better entry types
- identify safer or faster route variants
- quantify “missed winners” vs “wisely avoided losers”

---

## 14. Candidate promotion policy

No candidate becomes live automatically during first-20.

After first-20, a candidate may become `promotion_pending` only if:

- minimum shadow sample count achieved
- positive post-fee expectancy
- acceptable max drawdown
- acceptable win/loss clustering
- stable execution assumptions
- no dependency on one outlier event
- CEO review explicitly records approval status

**Promotion states:**

- `shadow_only`
- `promotion_pending`
- `approved_for_paper_live`
- `approved_for_probe_live`
- `approved_live`
- `rejected`

---

## 15. First live trade policy

Trade 1 must be a Route A trade unless no valid Route A setup appears and the operator explicitly overrides.

**Trade 1 objectives:**

- verify order path
- verify fill recording
- verify closed-trade persistence
- verify score generation
- verify CEO/learning hooks
- verify dashboard updates

Trade 1 is a system validation trade, not a performance trade.

---

## 16. First-20 report requirements

After trade 20, the report must include:

**summary**

- net PnL
- gross PnL
- fees
- route A vs B counts
- win rate by route
- average hold time by route
- execution cleanliness summary

**execution section**

- average signal-to-submit
- average submit-to-fill
- stale cancel count
- slippage distribution
- maker vs taker realized mix

**edge section**

- expected edge vs realized move
- expected move vs actual move
- false positives
- false negatives inferred from opportunity log

**risk section**

- max adverse excursion
- consecutive loss clusters
- session drawdown
- hard-stop events

**learning section**

- top three lessons
- top three route adjustments proposed
- top candidate promotions
- top risk-reduction recommendations

**verdict**

- `pass`
- `conditional_pass`
- `fail`

---

## 17. Pre-live acceptance checklist

Before live trade 1, all must be true:

- fee snapshot fetched and persisted
- market WS fresh
- user WS healthy or explicitly degraded with allowed fallback
- route metadata writing enabled
- execution timestamps enabled
- local databank write verified
- dashboard writing verified
- CEO snapshot verified
- learning hook verified
- shadow exploration logging verified
- hard-stop computation verified

If any item fails, do not begin first-20 live batch.
