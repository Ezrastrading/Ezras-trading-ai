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
