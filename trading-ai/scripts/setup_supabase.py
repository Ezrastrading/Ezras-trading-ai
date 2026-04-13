#!/usr/bin/env python3
"""Run once in Supabase SQL editor (or psql) to create shark_state storage for remote deploys."""

SQL = """
-- Ezras Shark — JSON state mirror (capital, positions, gaps, …)
create table if not exists public.shark_state (
  key text primary key,
  value jsonb not null,
  updated_at timestamptz not null default now()
);

alter table public.shark_state enable row level security;

-- Service role (SUPABASE_KEY) bypasses RLS for server-side sync.
-- For anon/authenticated clients, add policies as needed.

comment on table public.shark_state is 'Ezras Trading AI shark JSON blobs keyed by logical name';
"""


def main() -> None:
    print("Paste and execute the following in Supabase → SQL Editor:\n")
    print(SQL)


if __name__ == "__main__":
    main()
