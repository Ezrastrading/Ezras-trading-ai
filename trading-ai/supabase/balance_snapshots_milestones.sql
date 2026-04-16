-- Run in Supabase SQL Editor (optional; mirrors million_tracker / balance logging)
CREATE TABLE IF NOT EXISTS balance_snapshots (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  coinbase_balance FLOAT,
  kalshi_balance FLOAT,
  total_balance FLOAT,
  pct_to_million FLOAT
);

CREATE TABLE IF NOT EXISTS milestones (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  amount FLOAT,
  date_hit DATE,
  days_from_start INTEGER
);
