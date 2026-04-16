-- Run in Supabase SQL editor or as a migration.

CREATE TABLE IF NOT EXISTS lessons (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  session TEXT,
  platform TEXT,
  lesson TEXT,
  cost FLOAT,
  category TEXT,
  applied BOOLEAN DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS progression (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  platform TEXT,
  gate TEXT,
  product_id TEXT,
  pnl_usd FLOAT,
  exit_reason TEXT,
  hold_seconds INTEGER,
  balance_after FLOAT,
  win BOOLEAN
);

CREATE TABLE IF NOT EXISTS ceo_briefings (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  briefing_text TEXT,
  total_trades INTEGER,
  total_pnl FLOAT,
  win_rate FLOAT,
  balance FLOAT,
  lessons_count INTEGER
);
