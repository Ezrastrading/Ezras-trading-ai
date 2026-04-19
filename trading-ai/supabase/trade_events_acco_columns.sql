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

CREATE INDEX IF NOT EXISTS idx_trade_events_edge_id ON trade_events (edge_id) WHERE edge_id IS NOT NULL;
