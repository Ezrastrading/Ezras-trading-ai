-- Live micro lifecycle events (non-trade, append-only audit).
-- Safe for service-role writes; RLS should be service-role only (see rls_security_advisor_fix.sql posture).

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

-- Service-role only: default deny for anon/authenticated.
drop policy if exists "service_role_write" on public.live_micro_events;
create policy "service_role_write" on public.live_micro_events
  for all
  to public
  using (auth.role() = 'service_role')
  with check (auth.role() = 'service_role');

