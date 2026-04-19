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
