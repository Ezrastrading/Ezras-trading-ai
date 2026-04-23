-- ##############################################################################
-- ALL_REQUIRED_LIVE_MIGRATIONS.sql
-- Ezras Trading AI — concatenation of MIGRATION_ORDER.txt steps 1–3 (CRITICAL).
-- Run in Supabase → SQL Editor as ONE script, OR run each section separately in order.
-- Safe to re-run: CREATE IF NOT EXISTS / ADD COLUMN IF NOT EXISTS.
-- After success, verify: SELECT * FROM public.trade_events LIMIT 1;
-- ##############################################################################

-- Trade Intelligence Databank — run in Supabase SQL Editor.
-- Avenue map: A=coinbase, B=kalshi, C=tastytrade (application-enforced).

CREATE TABLE IF NOT EXISTS trade_events (
    trade_id text PRIMARY KEY,
    avenue_id text NOT NULL,
    avenue_name text NOT NULL,
    asset text NOT NULL,
    strategy_id text NOT NULL,
    route_chosen text,
    route_a_score double precision,
    route_b_score double precision,
    rejected_route text,
    rejected_reason text,
    regime text,
    spread_bps_entry double precision,
    volatility_bps_entry double precision,
    expected_edge_bps double precision,
    expected_fee_bps double precision,
    expected_net_edge_bps double precision,
    intended_entry_price double precision,
    actual_entry_price double precision,
    entry_slippage_bps double precision,
    entry_order_type text,
    maker_taker text,
    fill_seconds double precision,
    partial_fill_count integer,
    stale_cancelled boolean,
    intended_exit_price double precision,
    actual_exit_price double precision,
    exit_reason text,
    exit_slippage_bps double precision,
    hold_seconds double precision,
    gross_pnl double precision,
    fees_paid double precision,
    net_pnl double precision,
    shadow_price double precision,
    shadow_diff_bps double precision,
    discipline_ok boolean,
    degraded_mode boolean,
    health_state text,
    execution_score double precision,
    edge_score double precision,
    discipline_score double precision,
    trade_quality_score double precision,
    reward_delta double precision,
    penalty_delta double precision,
    anomaly_flags jsonb,
    timestamp_open timestamptz,
    timestamp_close timestamptz,
    created_at timestamptz DEFAULT now(),
    schema_version text NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_trade_events_avenue ON trade_events (avenue_id);
CREATE INDEX IF NOT EXISTS idx_trade_events_strategy ON trade_events (strategy_id);
CREATE INDEX IF NOT EXISTS idx_trade_events_close ON trade_events (timestamp_close);

CREATE TABLE IF NOT EXISTS daily_trade_summary (
    summary_id text PRIMARY KEY,
    summary_date text NOT NULL,
    avenue_id text,
    strategy_id text,
    trade_count integer,
    win_rate double precision,
    avg_win double precision,
    avg_loss double precision,
    gross_pnl double precision,
    fees_paid double precision,
    net_pnl double precision,
    maker_pct double precision,
    taker_pct double precision,
    avg_spread_bps double precision,
    avg_slippage_bps double precision,
    avg_hold_seconds double precision,
    cancel_rate_pct double precision,
    stale_pending_rate_pct double precision,
    created_at timestamptz DEFAULT now(),
    schema_version text NOT NULL
);

-- Weekly/monthly rollups use the same summary_id namespace as daily (unique keys).

CREATE TABLE IF NOT EXISTS strategy_performance_summary (
    strategy_summary_id text PRIMARY KEY,
    avenue_id text NOT NULL,
    strategy_id text NOT NULL,
    period_type text NOT NULL,
    start_ts timestamptz,
    end_ts timestamptz,
    trade_count integer,
    win_rate double precision,
    avg_win double precision,
    avg_loss double precision,
    gross_pnl double precision,
    fees_paid double precision,
    net_pnl double precision,
    expectancy double precision,
    best_regime text,
    worst_regime text,
    avg_execution_score double precision,
    avg_edge_score double precision,
    avg_trade_quality_score double precision,
    created_at timestamptz DEFAULT now(),
    schema_version text NOT NULL
);

CREATE TABLE IF NOT EXISTS avenue_performance_summary (
    avenue_summary_id text PRIMARY KEY,
    avenue_id text NOT NULL,
    avenue_name text NOT NULL,
    period_type text NOT NULL,
    start_ts timestamptz,
    end_ts timestamptz,
    trade_count integer,
    net_pnl double precision,
    gross_pnl double precision,
    fees_paid double precision,
    avg_trade_quality_score double precision,
    strongest_strategy_id text,
    weakest_strategy_id text,
    avg_spread_bps double precision,
    avg_slippage_bps double precision,
    created_at timestamptz DEFAULT now(),
    schema_version text NOT NULL
);

CREATE TABLE IF NOT EXISTS goal_progress_snapshots (
    snapshot_id text PRIMARY KEY,
    active_goal text,
    current_equity double precision,
    rolling_7d_net_profit double precision,
    rolling_30d_net_profit double precision,
    avenue_a_contribution double precision,
    avenue_b_contribution double precision,
    avenue_c_contribution double precision,
    current_speed_label text,
    blockers jsonb,
    top_actions jsonb,
    created_at timestamptz DEFAULT now(),
    schema_version text NOT NULL
);

CREATE TABLE IF NOT EXISTS ceo_review_snapshots (
    ceo_snapshot_id text PRIMARY KEY,
    review_type text,
    active_goal text,
    best_avenue text,
    weakest_avenue text,
    best_strategy text,
    weakest_strategy text,
    top_actions jsonb,
    top_risks jsonb,
    top_research_priorities jsonb,
    open_action_count integer,
    created_at timestamptz DEFAULT now(),
    schema_version text NOT NULL
);

-- Optional: enable Realtime for trade_events in Supabase Dashboard if needed.

-- ##############################################################################
-- SECTION 2 OF 3: edge_validation_engine.sql (requires Section 1)
-- ##############################################################################

-- Edge Validation & Promotion — optional Supabase mirror (run after trade_intelligence_databank.sql).

ALTER TABLE trade_events
  ADD COLUMN IF NOT EXISTS edge_id text,
  ADD COLUMN IF NOT EXISTS edge_lane text,
  ADD COLUMN IF NOT EXISTS market_snapshot_json jsonb;

CREATE INDEX IF NOT EXISTS idx_trade_events_edge_id ON trade_events (edge_id);

CREATE TABLE IF NOT EXISTS edge_registry (
    edge_id text PRIMARY KEY,
    avenue text NOT NULL,
    edge_type text NOT NULL,
    hypothesis_text text NOT NULL,
    required_conditions jsonb,
    status text NOT NULL,
    confidence double precision,
    linked_strategy_id text,
    source_research_ts timestamptz,
    source text,
    created_at timestamptz,
    updated_at timestamptz,
    notes text,
    rejection_reason text,
    promotion_history jsonb,
    schema_version text NOT NULL DEFAULT '1.0.0'
);

CREATE INDEX IF NOT EXISTS idx_edge_registry_avenue ON edge_registry (avenue);
CREATE INDEX IF NOT EXISTS idx_edge_registry_status ON edge_registry (status);

-- ##############################################################################
-- SECTION 3 OF 3: trade_events_acco_columns.sql (requires Section 2)
-- ##############################################################################

-- Optional migration: ACCO / trade-truth fields for trade_events (run in Supabase SQL Editor if mirroring).

ALTER TABLE trade_events ADD COLUMN IF NOT EXISTS edge_status_at_trade text;
ALTER TABLE trade_events ADD COLUMN IF NOT EXISTS instrument_kind text;
ALTER TABLE trade_events ADD COLUMN IF NOT EXISTS base_qty double precision;
ALTER TABLE trade_events ADD COLUMN IF NOT EXISTS quote_qty_buy double precision;
ALTER TABLE trade_events ADD COLUMN IF NOT EXISTS quote_qty_sell double precision;
ALTER TABLE trade_events ADD COLUMN IF NOT EXISTS avg_entry_price double precision;
ALTER TABLE trade_events ADD COLUMN IF NOT EXISTS avg_exit_price double precision;
ALTER TABLE trade_events ADD COLUMN IF NOT EXISTS contracts double precision;
ALTER TABLE trade_events ADD COLUMN IF NOT EXISTS entry_price_per_contract double precision;
ALTER TABLE trade_events ADD COLUMN IF NOT EXISTS payout_per_contract double precision;
ALTER TABLE trade_events ADD COLUMN IF NOT EXISTS entry_premium double precision;
ALTER TABLE trade_events ADD COLUMN IF NOT EXISTS exit_premium double precision;
ALTER TABLE trade_events ADD COLUMN IF NOT EXISTS option_multiplier double precision;
ALTER TABLE trade_events ADD COLUMN IF NOT EXISTS latency_ms double precision;
ALTER TABLE trade_events ADD COLUMN IF NOT EXISTS regime_bucket text;
ALTER TABLE trade_events ADD COLUMN IF NOT EXISTS execution_quality_score double precision;
ALTER TABLE trade_events ADD COLUMN IF NOT EXISTS research_source text;

-- idx_trade_events_edge_id: created in edge_validation_engine.sql (run that migration before this file).

-- ##############################################################################
-- OPTIONAL ADD-ON: live_micro_events (append-only lifecycle telemetry)
-- ##############################################################################

-- You can also run `supabase/live_micro_events.sql` separately.

create table if not exists public.live_micro_events (
  event_id text primary key,
  ts_unix double precision not null,
  event text not null,
  product_id text null,
  order_id text null,
  position_id text null,
  payload jsonb not null default '{}'::jsonb
);

alter table public.live_micro_events enable row level security;

drop policy if exists "service_role_write" on public.live_micro_events;
create policy "service_role_write" on public.live_micro_events
  for all
  to public
  using (auth.role() = 'service_role')
  with check (auth.role() = 'service_role');
